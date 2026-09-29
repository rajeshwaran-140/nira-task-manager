"""Bilingual (English + Tamil) API errors: {"detail": {"code","en","ta"}}"""
from fastapi import Request
from fastapi.responses import JSONResponse

MESSAGES = {
    "invalid_credentials": (401, "Incorrect email or password.", "மின்னஞ்சல் அல்லது கடவுச்சொல் தவறாக உள்ளது."),
    "invalid_otp": (400, "The OTP is wrong or has expired.", "OTP தவறானது அல்லது காலாவதியாகிவிட்டது."),
    "otp_locked": (429, "Too many wrong attempts. Request a new OTP.", "தவறான முயற்சிகள் அதிகம். புதிய OTP கேளுங்கள்."),
    "otp_cooldown": (429, "Please wait a little before requesting another OTP.", "சிறிது நேரம் காத்திருந்து மீண்டும் OTP கேளுங்கள்."),
    "otp_too_many": (429, "Too many OTP requests. Try again later.", "OTP கோரிக்கைகள் அதிகம். பிறகு முயற்சிக்கவும்."),
    "invalid_link": (400, "This link is invalid or has expired.", "இணைப்பு தவறானது அல்லது காலாவதியாகிவிட்டது."),
    "email_taken": (409, "This email is already registered.", "இந்த மின்னஞ்சல் ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது."),
    "phone_taken": (409, "This phone number belongs to another account.", "இந்த தொலைபேசி எண் வேறொரு கணக்கில் உள்ளது."),
    "invalid_phone": (422, "Enter a valid phone number (e.g. +91XXXXXXXXXX).", "சரியான தொலைபேசி எண்ணை உள்ளிடவும் (எ.கா. +91XXXXXXXXXX)."),
    "unauthorized": (401, "Please sign in again.", "மீண்டும் உள்நுழையவும்."),
    "account_disabled": (403, "This account is disabled.", "இந்த கணக்கு முடக்கப்பட்டுள்ளது."),
    "not_found": (404, "Not found.", "கிடைக்கவில்லை."),
    "sms_failed": (502, "Could not send the SMS. Try again later.", "SMS அனுப்ப முடியவில்லை. பிறகு முயற்சிக்கவும்."),
    "email_failed": (502, "Could not send the email. Try again later.", "மின்னஞ்சல் அனுப்ப முடியவில்லை. பிறகு முயற்சிக்கவும்."),
    "rate_limited": (429, "Too many requests. Please slow down.", "கோரிக்கைகள் மிக அதிகம். சிறிது நேரம் காத்திருக்கவும்."),
    "validation_error": (422, "Please check the details you entered.", "உள்ளிட்ட விவரங்களைச் சரிபார்க்கவும்."),
    "phone_not_verified": (400, "Verify your phone number first; NIRA needs it to call you.", "முதலில் உங்கள் தொலைபேசி எண்ணை சரிபார்க்கவும்; NIRA அழைக்க அது தேவை."),
    "consent_required": (400, "Your consent is needed before NIRA can call you.", "NIRA உங்களை அழைக்க உங்கள் ஒப்புதல் தேவை."),
    "ai_not_configured": (400, "AI is not set up. Add your API key in Settings → AI.", "AI அமைக்கப்படவில்லை. அமைப்புகள் → AI இல் உங்கள் API key ஐச் சேர்க்கவும்."),
    "key_required": (422, "Enter an API key for this provider.", "இந்த வழங்குநருக்கான API key ஐ உள்ளிடவும்."),
    "key_unreadable": (409, "Your saved key can't be read (the server key changed). Please save your key again.", "சேமித்த key ஐப் படிக்க முடியவில்லை (சர்வர் key மாறியது). உங்கள் key ஐ மீண்டும் சேமிக்கவும்."),
    "encryption_not_configured": (503, "Secure key storage is not set up on this server. Ask the admin to set ENCRYPTION_KEY.", "இந்த சர்வரில் பாதுகாப்பான key சேமிப்பு அமைக்கப்படவில்லை. நிர்வாகியிடம் ENCRYPTION_KEY அமைக்கச் சொல்லுங்கள்."),
    "encryption_key_invalid": (503, "The server's ENCRYPTION_KEY is invalid. Ask the admin to fix it.", "சர்வரின் ENCRYPTION_KEY தவறானது. நிர்வாகியிடம் சரிசெய்யச் சொல்லுங்கள்."),
    "invalid_key": (400, "The provider rejected this API key. Check it and try again.", "இந்த API key ஐ வழங்குநர் ஏற்கவில்லை. சரிபார்த்து மீண்டும் முயற்சிக்கவும்."),
    "quota_exceeded": (429, "The provider says this key is out of quota or rate-limited. Try later or check billing.", "இந்த key இன் quota முடிந்துவிட்டது அல்லது கோரிக்கைகள் அதிகம். பிறகு முயற்சிக்கவும் அல்லது billing ஐச் சரிபார்க்கவும்."),
    "model_not_found": (400, "The provider does not know this model name. Check the model field.", "இந்த model பெயரை வழங்குநர் அறியவில்லை. model புலத்தைச் சரிபார்க்கவும்."),
    "network_error": (502, "Could not reach the AI provider. Check the connection and try again.", "AI வழங்குநரை அடைய முடியவில்லை. இணைப்பைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும்."),
    "timeout": (504, "The AI provider took too long to answer. Try again.", "AI வழங்குநர் பதிலளிக்க அதிக நேரம் எடுத்தது. மீண்டும் முயற்சிக்கவும்."),
    "provider_error": (502, "The AI provider returned an error. Try again later.", "AI வழங்குநர் பிழையைத் தந்தது. பிறகு முயற்சிக்கவும்."),
    "action_expired": (410, "That request has expired. Please say it again.", "அந்தக் கோரிக்கை காலாவதியாகிவிட்டது. மீண்டும் சொல்லுங்கள்."),
    "action_not_pending": (409, "That request was already confirmed or cancelled.", "அந்தக் கோரிக்கை ஏற்கனவே உறுதி செய்யப்பட்டது அல்லது ரத்து செய்யப்பட்டது."),
    "twilio_not_configured": (503, "Phone calls are not set up on this server. Ask the admin to set the Twilio settings.", "இந்த சர்வரில் தொலைபேசி அழைப்புகள் அமைக்கப்படவில்லை. நிர்வாகியிடம் Twilio அமைப்புகளைச் சொல்லுங்கள்."),
    "call_forbidden": (403, "Test calls are only available for the number set as NIRA_USER_PHONE_NUMBER. Verify that number on your account first.", "NIRA_USER_PHONE_NUMBER ஆக அமைத்த எண்ணுக்கு மட்டுமே சோதனை அழைப்பு கிடைக்கும். முதலில் அந்த எண்ணை உங்கள் கணக்கில் சரிபார்க்கவும்."),
    "call_failed": (502, "Could not place the call. Check the Twilio settings and try again later.", "அழைப்பை உருவாக்க முடியவில்லை. Twilio அமைப்புகளைச் சரிபார்த்து பிறகு முயற்சிக்கவும்."),
    "internal": (500, "Something went wrong. Please try again.", "ஏதோ தவறு நடந்தது. மீண்டும் முயற்சிக்கவும்."),
}


class AppError(Exception):
    def __init__(self, code: str, **extra):
        self.code = code if code in MESSAGES else "internal"
        self.extra = extra


def body(code: str, **extra) -> dict:
    status, en, ta = MESSAGES[code]
    return {"detail": {"code": code, "en": en, "ta": ta, **extra}}


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(body(exc.code, **exc.extra), status_code=MESSAGES[exc.code][0])


async def validation_handler(request: Request, exc):
    # Only loc + msg: never echo the submitted input (could contain passwords / keys).
    errs = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(body("validation_error", errors=errs), status_code=422)


async def rate_limit_handler(request: Request, exc):
    return JSONResponse(body("rate_limited"), status_code=429)


async def unhandled_handler(request: Request, exc: Exception):
    import logging
    logging.getLogger("nira").exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(body("internal"), status_code=500)
