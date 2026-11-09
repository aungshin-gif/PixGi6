# Activation Telegram Bot

Wallet မပါ။ Login website မပါ။ Customer key → backend Real key → Pixel API.
Python 3.10+ (Linux) နဲ့ SQLite သုံးထားပြီး pip package မလိုပါ။ Domain၊ HTTPS form၊ public inbound port မလိုပါ။ Telegram နဲ့ Pixel API ကို outbound HTTPS ချိတ်နိုင်ရပါမယ်။

## အမြန်စမ်းရန်

1. Telegram `@BotFather` မှာ `/newbot` နဲ့ bot တစ်ခုလုပ်ပါ။
2. VPS မှာ archive ဖြည်ပြီး project directory ထဲဝင်ပါ။
3. `cp config.example.json config.json` လုပ်ပါ။
4. `config.json` မှာ bot token၊ ကိုယ့် numeric Telegram user ID၊ Real key ထည့်ပါ။ Secret တွေကို chat ထဲ မပို့ပါနဲ့။ Real key များစွာရှိရင် `real_keys` မှာ `main`, `second` စသဖြင့် alias နဲ့ထည့်နိုင်ပါတယ်။
5. `chmod 600 config.json` လုပ်ပြီး `python3 bot.py` နဲ့ run ပါ။ Telegram ID မသိသေးရင် အခြား user မဟုတ်တဲ့ placeholder admin ID နဲ့ စတင်ပြီး ကိုယ့် bot ကို `/myid` ပို့ပါ။ ပြန်ရတဲ့ ID ကို admin_ids မှာထည့်ပြီး restart ပါ။
6. ကိုယ့် bot ရဲ့ private chat မှာ admin က `/newkey main 1` ပို့ပါ။ တစ်ကြိမ်သုံး customer key ပြန်ရပါမယ်။ `/newkey main 5` ဆို ငါးကြိမ်သုံး key ပါ။
7. Customer ကို အဲဒီ key ပဲပေးပါ။ Customer က `/redeem KAR-...` ပို့ပြီး Activate နှိပ်ပါ။

Customer က message တစ်စောင်ထဲ တစ်ကြောင်းစီ ပို့ရန်:

```text
email@example.com
account-password
AUTHENTICATOR_BASE32_SECRET
recovery@example.com
```

နောက်ဆုံး recovery email ကိုမထည့်လည်းရပါတယ်။ 2FA က rotating 6-digit code မဟုတ်ဘဲ authenticator secret ဖြစ်ရပါမယ်။ လက်ရှိ input က TOTP သာ support လုပ်ပါတယ်။ Service က 2FA မတောင်းတဲ့အချိန် email/password နှစ်ကြောင်းနဲ့လည်း တင်နိုင်ပါတယ်။

## Customer commands

- `/start`: Help နဲ့ keyboard
- `/redeem KEY`: Customer key ချိတ်မယ်။ ပထမ redeem လုပ်တဲ့ Telegram user နဲ့ bind ဖြစ်မယ်။
- `/activate`: Account input စောင့်မယ်။ Input prompt က 10 မိနစ်ကြာရင် သက်တမ်းကုန်မယ်။ Restart ပြီးရင် `/activate` ထပ်နှိပ်ရမယ်။
- `/balance`: လက်ရှိ customer key လက်ကျန်
- `/orders`: နောက်ဆုံး order 10 ခု
- `/cancel`: Input ကိုသာပယ်ဖျက်မယ်။ Upstream task ကို cancel မလုပ်ပါ။
- `/myid`: ကိုယ့် Telegram ID

## Quota နဲ့ recovery

Customer key ထုတ်ခြင်းက upstream quota ကို မတိုးစေပါ။ Quota ရှိတဲ့ Real key ကို admin က ဝယ်/ဖြည့်ထားရပါမယ်။ Bot အပြင်ကသုံးနေရင် upstream လက်ကျန် ပြောင်းနိုင်ပါတယ်။ Customer key တွေကိုလည်း upstream capacity ထက်ပိုပြီး မရောင်းမိဖို့ admin ကစီမံရပါမယ်။

Task တင်ချိန် customer quota တစ်ကြိမ် reserve လုပ်တယ်။ Success ဆို ဆက်ဖြတ်ထားတယ်။ Explicit rejection၊ failed၊ cancelled၊ timeout ဆို တစ်ကြိမ်ပဲပြန်ဖြည့်တယ်။ API က subscription တကယ်ရမရကို bot က သီးခြားမစစ်ပါ။ Upstream status ကိုပြပါတယ်။

Connection timeout၊ malformed response၊ generic server error၊ submission အလယ်မှာ restart ဖြစ်ခြင်းတို့မှာ task တင်ပြီး/မပြီး မသေချာလို့ `unknown` ထားပြီး quota ကိုဖယ်ထားဆဲဖြစ်မယ်။ ထပ်တင်ခြင်း/အလိုအလျောက် refund မလုပ်ပါ။ Admin က `/pending` နဲ့ပုံမှန်စစ်ပါ။

- Upstream history မှာ သက်ဆိုင်ရာ task ရှိတာစစ်ပြီး `/attach ORDER_ID TASK_UUID` လုပ်ပါ။ အဲဒီ Real key အောက်က matching email ဖြစ်မှ ချိတ်ပေးမယ်။
- Task မရှိဘဲ quota လည်းမကုန်ကြောင်း provider ဘက်မှာအတည်ပြုပြီးမှ `/refund ORDER_ID CONFIRM` လုပ်ပါ။ ဒီ command က manual admin override ဖြစ်ပြီး upstream refund မလုပ်ပါ။
- Real key ကို replace လုပ်ရင် task history ထိန်းထားတဲ့ replacement key ကို config ရဲ့ **မူရင်း alias တစ်ခုတည်း** မှာပြောင်းပြီး restart ပါ။ Key အသစ်မဆက်စပ်တဲ့ key နဲ့ alias ကို မအစားထိုးပါနဲ့။

Task history ကို Real key တစ်ခုစီအလိုက် pagination နဲ့ 15 စက္ကန့်ခန့်ခြားစစ်တယ်။ Page များရင် ပိုကြာနိုင်ပါတယ်။ API calls ကို bridge 1.15 seconds၊ keys 3.2 seconds ခြားထားတယ်။ VPS IP တူတဲ့ အခြား app တွေက rate limit ကိုမျှသုံးနေတာ bot က မသိနိုင်ပါ။ Bot instance တစ်ခုတည်းပဲ run ပါ။ Database backup အတွက် bot ရပ်ပြီး `state.sqlite3` နဲ့ config ကို အတူသိမ်းပါ။ DB ပျောက်ရင် customer balances နဲ့ orders ပျောက်ပါတယ်။

## VPS ပေါ် အမြဲ run ရန် (Ubuntu/Debian + systemd)

Project files ကို `/opt/activation-bot` ထဲထားပြီး အောက်ပါ commands ကို root/sudo နဲ့ run ပါ:

```sh
sudo useradd --system --home-dir /opt/activation-bot --shell /usr/sbin/nologin activationbot
sudo chown -R activationbot:activationbot /opt/activation-bot
sudo chmod 700 /opt/activation-bot
sudo chmod 600 /opt/activation-bot/config.json
sudo cp /opt/activation-bot/activation-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now activation-bot
sudo systemctl status activation-bot
```

Foreground bot ကိုအရင်ပိတ်ပါ။ VPS restart ဖြစ်လည်း systemd က ပြန်တင်ပေးမယ်။ Webhook configure လုပ်ထားတဲ့ existing bot token ဆို getUpdates long polling မရပါ။ Bot အသစ်သုံးပါ သို့မဟုတ် existing webhook ကို owner ကပယ်ဖျက်ပါ။

## အချက်အလက်သိမ်းဆည်းမှု

Telegram messages ထဲမှာ password နဲ့ 2FA secret ကျန်မယ်။ Backend က အဲဒါတွေကို database သို့မဟုတ် application logs ထဲမရေးဘဲ upstream ကိုပို့ပြီး လက်ရှိ request အတွင်းပဲ ကိုင်တယ်။ Telegram Bot API traffic ကို URL/body အပြည့်အစုံ log လုပ်တဲ့ proxy မသုံးပါနဲ့။ Python memory ထဲက secret တွေကို zeroize လုပ်ပေးနိုင်တယ်လို့ မအာမခံပါ။ Customer key ကို DB မှာ SHA-256 hash နဲ့သိမ်းတယ်။ Real key နဲ့ bot token က permissions ကန့်သတ်ထားတဲ့ config.json ထဲမှာရှိတယ်။ Order email နဲ့ result link က DB ထဲကျန်မယ်။

Google account password နဲ့ 2FA secret ကို upstream provider ကလက်ခံရမှာဖြစ်လို့ ကိုယ်ပိုင် သို့မဟုတ် ပိုင်ရှင်ခွင့်ပြုထားတဲ့ account သာသုံးပါ။

## စမ်းသပ်မှု

```sh
python3 -m unittest -v
```

Fake Telegram/Pixel responses နဲ့ ownership၊ admin access၊ quota/refund၊ duplicate submit၊ uncertain network outcome နဲ့ crash recovery ကိုစမ်းထားပါတယ်။ Real bot token/API key နဲ့ live activation မစမ်းရသေးပါ။ Provider ကိုယုံကြည်စိတ်ချရမှု သို့မဟုတ် VPS IP ချိတ်ဆက်နိုင်မှုကို အတည်ပြုထားခြင်းမရှိပါ။
