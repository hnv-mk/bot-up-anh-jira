import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import re
import io
import urllib.parse
from PIL import Image
from flask import Flask
import threading
import os

# ==========================================
# CẤU HÌNH BOT VÀ API JIRA
# ==========================================
TELEGRAM_TOKEN = "8622942090:AAF1vPF-D5tFMx_pGdL-mVMEvAHHzxI-a0c"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

JIRA_USER = "minhkhang_dat@shlx.vn"
JIRA_PASS = "Anhduc9683@"
JIRA_TOKEN = ""

# Bộ nhớ tạm để lưu người dùng đang chọn học viên nào
user_states = {}

# ==========================================
# HÀM XỬ LÝ ẢNH & ĐĂNG NHẬP
# ==========================================
def login_jira():
    global JIRA_TOKEN
    res = requests.post("https://jira.shlx.vn/v1/login", json={"email": JIRA_USER, "password": JIRA_PASS}, headers={"x-client-app": "jira"})
    if res.status_code == 200:
        JIRA_TOKEN = f"Bearer {res.json().get('access_token')}"
        return True
    return False

def compress_image_from_bytes(image_bytes):
    if len(image_bytes) <= 1024 * 1024:
        return image_bytes, "image/jpeg"
        
    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode != 'RGB': 
            img = img.convert('RGB')
        img.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=75, optimize=True)
        return output.getvalue(), "image/jpeg"

# ==========================================
# PHA 1: NHẬN CHỮ -> TÌM HỌC VIÊN
# ==========================================
@bot.message_handler(content_types=['text'])
def handle_text_search(message):
    global JIRA_TOKEN
    chat_id = message.chat.id
    raw_text = message.text.strip()
    
    cccd_match = re.search(r'\b\d{9}\b|\b\d{12}\b', raw_text)
    cccd = cccd_match.group(0) if cccd_match else ""
    
    noise_patterns = [
        r'(?i)nhờ tổ dat cập nhật ảnh hv', r'(?i)nhờ vp update ảnh hv', 
        r'(?i)nhờ vp update', r'(?i)nhờ vp', r'(?i)nhờ cập nhật', 
        r'(?i)cập nhật ảnh', r'(?i)ảnh hv', r'(?i)đang bị nhận diện kém', 
        r'(?i)nhận diện kém', r'-\s*\[\s*\]'
    ]
    
    clean = raw_text
    if cccd: clean = clean.replace(cccd, '')
    for p in noise_patterns: clean = re.sub(p, '', clean)
        
    clean = re.sub(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', '', clean)
    clean = re.sub(r'\b\d{1,2}:\d{2}\b', '', clean)
    
    class_match = re.search(r'\b[A-Za-z]{1,2}\d{1,2}[A-Za-z0-9]*\b', clean)
    if class_match: clean = clean.replace(class_match.group(0), '')
    
    parts = re.split(r'[,;\(\)\.]', clean)
    name = ""
    for part in parts:
        part = re.sub(r'[^\w\s]', '', part)
        part = re.sub(r'\d+', '', part)
        part_clean = re.sub(r'\s+', ' ', part).strip()
        if part_clean: 
            name = part_clean
            break
    
    search_query = cccd if cccd else name
    
    if not search_query:
        bot.reply_to(message, "⚠️ Không tìm thấy tên hoặc CCCD để tra cứu.")
        return

    msg_status = bot.reply_to(message, f"🔎 Đang tìm kiếm học viên: **{search_query}**...", parse_mode="Markdown")
    
    if not JIRA_TOKEN: login_jira()

    url_search = f"https://jira.shlx.vn/v1/trainees?name={urllib.parse.quote(search_query)}&page=1"
    res_search = requests.get(url_search, headers={"Authorization": JIRA_TOKEN})
    
    if res_search.status_code != 200:
        bot.edit_message_text("❌ Lỗi kết nối đến API Jira.", chat_id, msg_status.message_id)
        return
        
    items = res_search.json().get("items", [])
    if not items:
        bot.edit_message_text(f"❌ Không tìm thấy ai có tên/CCCD: {search_query} trên hệ thống Jira.", chat_id, msg_status.message_id)
        return

    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    
    for item in items:
        trainee_id = str(item.get("id", ""))
        ho_ten = item.get("ho_va_ten", "")
        khoa = item.get("course_name", "")
        markup.add(InlineKeyboardButton(text=f"{ho_ten} - {khoa}", callback_data=f"up_{trainee_id}_{ho_ten}"))
        
    bot.edit_message_text(f"✅ Đã tìm thấy **{len(items)}** kết quả.\n👇 Bấm chọn học viên để UP ẢNH:", chat_id, msg_status.message_id, reply_markup=markup, parse_mode="Markdown")

# ==========================================
# PHA TẦM TRUNG: CHỌN HỌC VIÊN
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data.startswith('up_'))
def handle_select_trainee(call):
    chat_id = call.message.chat.id
    data = call.data.split('_')
    trainee_id = data[1]
    ho_ten = data[2]
    
    user_states[chat_id] = {"trainee_id": trainee_id, "ho_ten": ho_ten}
    bot.edit_message_text(f"🎯 Bạn đang chọn up ảnh cho: **{ho_ten}**\n\n📸 Hãy gửi hoặc Forward (chuyển tiếp) các bức ảnh vào đây ngay nào!", chat_id, call.message.message_id, parse_mode="Markdown")

# ==========================================
# PHA 2: NHẬN ẢNH -> NÉN -> UPLOAD JIRA
# ==========================================
@bot.message_handler(content_types=['photo', 'document'])
def handle_photos(message):
    global JIRA_TOKEN
    chat_id = message.chat.id
    
    if chat_id not in user_states:
        bot.reply_to(message, "⚠️ Bạn chưa chọn học viên nào! Hãy gửi tên/CCCD trước nhé.")
        return
        
    trainee_id = user_states[chat_id]["trainee_id"]
    ho_ten = user_states[chat_id]["ho_ten"]
    
    msg_status = bot.reply_to(message, f"⏳ Đang nén ảnh và đẩy lên Jira cho **{ho_ten}**...", parse_mode="Markdown")
    
    try:
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.content_type == 'document' and message.document.mime_type.startswith('image/'):
            file_id = message.document.file_id
        else:
            bot.edit_message_text("❌ File gửi lên không phải là ảnh hợp lệ.", chat_id, msg_status.message_id)
            return

        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        img_bytes, mime_type = compress_image_from_bytes(downloaded_file)
        
        if not JIRA_TOKEN: login_jira()
        url_upload = f"https://jira.shlx.vn/v1/trainees/{trainee_id}/faces2"
        files = {"files": ("telegram_photo.jpg", img_bytes, mime_type)}
        
        res_upload = requests.post(url_upload, headers={"Authorization": JIRA_TOKEN}, files=files)
        
        if 200 <= res_upload.status_code < 300:
            bot.edit_message_text(f"✅ THÀNH CÔNG! Đã up ảnh cực nét cho: **{ho_ten}**", chat_id, msg_status.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text(f"❌ Lỗi Upload từ Jira: {res_upload.text}", chat_id, msg_status.message_id)
            
    except Exception as e:
        bot.edit_message_text(f"❌ Có lỗi khi nén/up ảnh: {str(e)}", chat_id, msg_status.message_id)

# ==========================================
# LÁCH LUẬT RENDER ĐỂ CHẠY 24/7
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Web giả của Bot Up Ảnh đang hoạt động!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    t = threading.Thread(target=run_web)
    t.start()
    
    print("Bot Up Ảnh Jira đang chạy...")
    login_jira()
    bot.infinity_polling()
