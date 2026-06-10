# 🤖 Bot Setup Guide - Poora Step by Step

---

## 📁 Files Jo Chahiye

```
bot.py                  ← Main bot file
requirements.txt        ← Libraries
generate_session.py     ← Session banane ke liye (sirf ek baar)
thumbnail.jpg           ← Optional (apni thumbnail)
```

---

## STEP 1 — Cheezein Collect Karo

### A) Bot Token (@BotFather se)
1. Telegram pe @BotFather kholo
2. `/newbot` bhejo
3. Naam daal do
4. Username daal do (end mein "bot" hona chahiye)
5. Token copy kar lo — yeh aayega:
   `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxx`

### B) API ID & API Hash (my.telegram.org se)
1. https://my.telegram.org pe jaao
2. Apne number se login karo
3. "API development tools" click karo
4. App create karo (kuch bhi naam daal sakte ho)
5. `api_id` aur `api_hash` copy kar lo

### C) Admin ID (tera Telegram ID)
1. @userinfobot pe jaao Telegram pe
2. `/start` bhejo
3. "Id:" wala number copy kar lo
   Example: `987654321`

---

## STEP 2 — Session String Banao (PC Pe - Ek Baar)

1. `generate_session.py` mein apna API_ID, API_HASH, PHONE daalo
2. Run karo:
   ```
   pip install pyrogram TgCrypto
   python generate_session.py
   ```
3. Phone pe OTP aayega → daalo
4. Ek lamba string milega → copy karke safe rakh lo

---

## STEP 3 — Railway Pe Deploy Karo

1. **GitHub repo banao**
   - github.com pe jaao → New Repository
   - `bot.py` aur `requirements.txt` upload karo
   - `generate_session.py` mat daalo (sensitive hai)

2. **Railway pe jaao** — railway.app
   - "New Project" → "Deploy from GitHub"
   - Apna repo select karo

3. **Environment Variables Set Karo**
   Railway dashboard → Variables tab:

   ```
   BOT_TOKEN           = 7123456789:AAHxxxxxx
   API_ID              = 123456
   API_HASH            = abcdef1234567890abcdef
   SESSION_STRING      = BQANsxkAVp3q.... (woh lamba string)
   ADMIN_ID            = 987654321
   FORCE_JOIN_CHANNEL  = @YourChannel
   DAILY_LIMIT         = 10
   ```

   > FORCE_JOIN_CHANNEL khali chhod do agar force join nahi chahiye

4. **Deploy!**
   - Railway automatically deploy kar dega
   - Logs mein "✅ Bot chal raha hai!" dikhega

---

## STEP 4 — Test Karo

1. Apne bot ka username search karo Telegram pe
2. `/start` bhejo
3. Koi Diskwala link bhejo:
   `https://www.diskwala.com/app/6a10f19b69eabf8720cbda8f`
4. Video aani chahiye! ✅

---

## STEP 5 — Admin Panel Use Karo

Bot pe `/admin` bhejo:

| Button | Kaam |
|--------|------|
| ✏️ Caption | Video ka caption change karo |
| 🖼️ Thumbnail | Apni thumbnail set karo |
| 👋 Welcome Msg | Start message change karo |
| 👁️ Settings | Sab settings dekho |
| 📊 Stats | Users, downloads stats |
| 🚫 User Ban | Kisi ko ban karo |
| ✅ User Unban | Ban hatao |
| 📢 Broadcast | Sab users ko message bhejo |
| ⚙️ Daily Limit | Download limit change karo |

---

## Caption Mein Special Variable

Caption mein `{filename}` likhne par automatically video ka naam aa jaata hai.

Example caption:
```
🎬 {filename}

📥 @YourBotUsername se download kiya
💫 Enjoy karo!
```

---

## ⚠️ Important Notes

- `SESSION_STRING` kisi ko mat dena — teri personal account ka access hai usme
- Railway free plan pe 500 hours/month milte hain — paid plan lo agar 24/7 chahiye (~$5/month)
- Bot B (@BookTherepybot) se video aane mein 10-30 second lag sakte hain — normal hai
- Agar video 50MB se badi ho toh Telegram Bot API reject kar sakta hai

---

## ❓ Common Errors

| Error | Fix |
|-------|-----|
| `SESSION_STRING invalid` | Dobara generate_session.py chalao |
| `Bot token invalid` | @BotFather se naya token lo |
| `Chat not found` | Bot B username check karo |
| Video nahi aa rahi | Bot B ka response check karo manually |

