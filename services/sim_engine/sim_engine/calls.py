"""Scripted call transcripts: digital-arrest scam dialogues and benign calls.

Languages: ``en``, ``hi`` (Devanagari) and ``hi-Latn`` (romanised Hindi). Every call is a
sequence of transcript chunks (one ``CallEvent`` per chunk, shared ``call_id``).
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import numpy as np
from scam_contracts.models import CallEvent

from .world import World, stable_id

LANGS = ("en", "hi-Latn", "hi")
LANG_P = (0.5, 0.3, 0.2)

# ---------------------------------------------------------------- scam scripts
AUTHORITIES = {
    "cbi": ("CBI", "सीबीआई", "CBI"),
    "ed": ("the Enforcement Directorate", "प्रवर्तन निदेशालय (ED)", "Enforcement Directorate"),
    "customs": ("Mumbai Customs", "मुंबई कस्टम्स", "Mumbai Customs"),
    "police": ("the Mumbai Police Cyber Crime Branch", "साइबर क्राइम पुलिस", "Cyber Crime Police"),
    "trai": ("TRAI", "ट्राई (TRAI)", "TRAI"),
    "ncb": ("the Narcotics Control Bureau", "नारकोटिक्स कंट्रोल ब्यूरो", "Narcotics Bureau"),
}
PRETEXTS = {
    "parcel": (
        "a parcel booked in your name from Mumbai to Taiwan was seized with MDMA, five passports "
        "and your Aadhaar linked to it",
        "aapke naam se ek parcel pakda gaya hai jisme drugs, paanch passport aur aapka Aadhaar "
        "link mila hai",
        "आपके नाम से बुक किया गया एक पार्सल पकड़ा गया है जिसमें ड्रग्स, पाँच पासपोर्ट और आपका आधार लिंक मिला है",
    ),
    "sim": (
        "a SIM card registered on your Aadhaar was used for illegal advertisements and harassment "
        "and will be disconnected in two hours",
        "aapke Aadhaar par registered SIM se illegal calls aur harassment ho raha hai aur do "
        "ghante me number band ho jayega",
        "आपके आधार पर रजिस्टर्ड सिम से गैरकानूनी कॉल और उत्पीड़न हो रहा है और दो घंटे में नंबर बंद हो जाएगा",
    ),
    "laundering": (
        "your bank account is linked to a money laundering case with crores of rupees of "
        "hawala transactions and an arrest warrant has been issued",
        "aapka bank account ek money laundering case se juda hai, hawala ke karodon rupaye ka "
        "lenden hua hai aur arrest warrant nikal chuka hai",
        "आपका बैंक खाता मनी लॉन्ड्रिंग केस से जुड़ा है, हवाला के करोड़ों रुपये का लेनदेन हुआ है और "
        "गिरफ्तारी वारंट जारी हो चुका है",
    ),
}

S_INTRO = (
    [
        "Hello, am I speaking with {name}? This is Inspector {officer} calling from {auth}. "
        "This is an official call regarding a serious case registered against you.",
        "Namaste, I am {officer} from {auth}. Please stay on the line, this call concerns your "
        "Aadhaar and your case number {case}.",
        "This is an urgent call from {auth}. I am officer {officer}, badge number {case}. "
        "Please confirm your name.",
    ],
    [
        "Hello, kya main {name} ji se baat kar raha hoon? Main inspector {officer} bol raha hoon, "
        "{auth} se. Aapke khilaf ek gambhir case darj hua hai.",
        "Namaste, main {auth} se {officer} bol raha hoon. Line par bane rahiye, aapke Aadhaar "
        "aur case number {case} ka mamla hai.",
        "Yeh {auth} ki taraf se urgent call hai. Mera naam {officer} hai, badge number {case}. "
        "Apna naam confirm kijiye.",
    ],
    [
        "हेलो, क्या मैं {name} जी से बात कर रहा हूँ? मैं इंस्पेक्टर {officer} बोल रहा हूँ, {auth} से। "
        "आपके खिलाफ एक गंभीर मामला दर्ज हुआ है।",
        "नमस्ते, मैं {auth} से {officer} बोल रहा हूँ। लाइन पर बने रहिए, आपके आधार और केस नंबर "
        "{case} का मामला है।",
        "यह {auth} की तरफ से ज़रूरी कॉल है। मेरा नाम {officer} है, बैज नंबर {case}। अपना नाम कन्फर्म कीजिए।",
    ],
)
S_ALLEGE = (
    [
        "{name}, our investigation shows that {pretext}. This is a criminal offence under "
        "the PMLA and the NDPS Act.",
        "We have received a complaint and evidence: {pretext}. Your name is in the FIR.",
    ],
    [
        "{name} ji, hamari jaanch me saamne aaya hai ki {pretext}. Yeh PMLA aur NDPS act ke "
        "tahat criminal offence hai.",
        "Hamare paas complaint aur saboot hai: {pretext}. FIR me aapka naam hai.",
    ],
    [
        "{name} जी, हमारी जांच में सामने आया है कि {pretext}। यह PMLA और NDPS एक्ट के तहत आपराधिक अपराध है।",
        "हमारे पास शिकायत और सबूत हैं: {pretext}। FIR में आपका नाम है।",
    ],
)
S_ARREST = (
    [
        "You are now under digital arrest. You must remain on this video call at all times, "
        "keep your camera on and do not disconnect until the investigation is complete.",
        "Under Supreme Court orders you are placed under digital arrest. Do not leave the "
        "video call, do not switch off your camera, and do not step out of the room.",
    ],
    [
        "Aap abhi se digital arrest me hain. Aapko is video call par bane rehna hai, camera on "
        "rakhna hai aur investigation poori hone tak call disconnect nahi karni hai.",
        "Supreme Court ke order ke tahat aap digital arrest me hain. Video call mat kaatiye, "
        "camera band mat kijiye aur kamre se bahar mat jaiye.",
    ],
    [
        "आप अभी से डिजिटल अरेस्ट में हैं। आपको इस वीडियो कॉल पर बने रहना है, कैमरा ऑन रखना है और "
        "जांच पूरी होने तक कॉल डिस्कनेक्ट नहीं करनी है।",
        "सुप्रीम कोर्ट के आदेश के तहत आप डिजिटल अरेस्ट में हैं। वीडियो कॉल मत काटिए, कैमरा बंद "
        "मत कीजिए और कमरे से बाहर मत जाइए।",
    ],
)
S_SECRET = (
    [
        "This case is strictly confidential. Do not tell anyone, not your family, not your "
        "bank, not your lawyer. If you tell anyone it will be obstruction of justice.",
        "Keep this secret. Do not speak to anyone about this call, otherwise we will have to "
        "arrest your family members too.",
    ],
    [
        "Yeh case bilkul confidential hai. Kisi ko mat batana, na ghar walon ko, na bank ko, na "
        "vakeel ko. Kisi ko bataya to yeh obstruction of justice hoga.",
        "Yeh baat secret rakhiye. Is call ke baare me kisi se baat mat kijiye, warna aapke "
        "parivar walon ko bhi giraftaar karna padega.",
    ],
    [
        "यह मामला पूरी तरह गोपनीय है। किसी को मत बताना, न घरवालों को, न बैंक को, न वकील को। "
        "किसी को बताया तो यह न्याय में बाधा माना जाएगा।",
        "यह बात सीक्रेट रखिए। इस कॉल के बारे में किसी से बात मत कीजिए, वरना आपके परिवार को भी "
        "गिरफ्तार करना पड़ेगा।",
    ],
)
S_URGENT = (
    [
        "You have only thirty minutes. If you do not cooperate right now, a team will reach "
        "your house and arrest you immediately.",
        "This is very urgent. Your arrest warrant will be executed today unless you follow my "
        "instructions immediately.",
    ],
    [
        "Aapke paas sirf tees minute hain. Agar aap abhi cooperate nahi karte, to team aapke "
        "ghar pahunch kar turant giraftaar kar legi.",
        "Yeh bahut urgent hai. Aaj hi aapka arrest warrant execute ho jayega agar aap meri baat "
        "nahi maante.",
    ],
    [
        "आपके पास सिर्फ तीस मिनट हैं। अगर आप अभी सहयोग नहीं करते तो टीम आपके घर पहुंचकर तुरंत गिरफ्तार कर लेगी।",
        "यह बहुत ज़रूरी है। आज ही आपका गिरफ्तारी वारंट लागू हो जाएगा अगर आप मेरी बात नहीं मानते।",
    ],
)
S_VERIFY = (
    [
        "To prove your innocence we need to verify your account. Tell me your bank name, "
        "account balance and the UPI app you use, and share your Aadhaar and PAN details.",
        "We must verify your account and your savings. Please open your banking app on the "
        "video call and show me the balance.",
    ],
    [
        "Apni begunahi sabit karne ke liye hume aapka account verify karna hoga. Bank ka naam, "
        "balance aur UPI app batayiye, aur Aadhaar PAN details bhi share kijiye.",
        "Hume aapka account aur savings verify karni hai. Video call par apna banking app kholkar "
        "balance dikhaiye.",
    ],
    [
        "अपनी बेगुनाही साबित करने के लिए हमें आपका अकाउंट वेरिफाई करना होगा। बैंक का नाम, बैलेंस और "
        "UPI ऐप बताइए, और आधार पैन की जानकारी भी शेयर कीजिए।",
        "हमें आपका अकाउंट और बचत वेरिफाई करनी है। वीडियो कॉल पर अपना बैंकिंग ऐप खोलकर बैलेंस दिखाइए।",
    ],
)
S_TRANSFER = (
    [
        "For RBI verification you must transfer your funds to the RBI safe account I am sending "
        "now. The money will be checked and returned to you within 24 hours. Send it in "
        "parts by UPI right now.",
        "Transfer the amount to this safe account for verification. After the audit it will be "
        "refunded. Do it immediately and keep the call connected.",
    ],
    [
        "RBI verification ke liye aapko apne paise us RBI safe account me transfer karne honge jo "
        "main abhi bhej raha hoon. Paise check hoke 24 ghante me wapas mil jayenge. Abhi UPI se "
        "kisht me bhejiye.",
        "Verification ke liye yeh amount is safe account me transfer kijiye. Audit ke baad refund "
        "ho jayega. Turant kijiye aur call connected rakhiye.",
    ],
    [
        "RBI वेरिफिकेशन के लिए आपको अपने पैसे उस RBI सेफ अकाउंट में ट्रांसफर करने होंगे जो मैं अभी "
        "भेज रहा हूँ। पैसे चेक होकर 24 घंटे में वापस मिल जाएंगे। अभी UPI से किश्तों में भेजिए।",
        "वेरिफिकेशन के लिए यह रकम इस सेफ अकाउंट में ट्रांसफर कीजिए। ऑडिट के बाद रिफंड हो "
        "जाएगा। तुरंत कीजिए और कॉल कनेक्टेड रखिए।",
    ],
)
S_FOLLOW = (
    [
        "Good. Send the next installment as well, the first transfer is not enough. Stay on the "
        "video call and do not tell anyone.",
        "Have you sent it? Send the screenshot here. The verification amount is still short, "
        "transfer the remaining balance to the safe account.",
    ],
    [
        "Achha. Agli kisht bhi bhejiye, pehla transfer kaafi nahi hai. Video call par bane "
        "rahiye aur kisi ko mat batana.",
        "Bheja? Screenshot yahan bhejiye. Verification amount abhi kam hai, bacha hua balance "
        "safe account me transfer kijiye.",
    ],
    [
        "अच्छा। अगली किश्त भी भेजिए, पहला ट्रांसफर काफी नहीं है। वीडियो कॉल पर बने रहिए और किसी को "
        "मत बताना।",
        "भेजा? स्क्रीनशॉट यहाँ भेजिए। वेरिफिकेशन रकम अभी कम है, बचा हुआ बैलेंस सेफ अकाउंट में ट्रांसफर कीजिए।",
    ],
)
VICTIM_REPLIES = (
    [
        "Sir, I am very scared. Please tell me what I should do.",
        "Okay sir, I am on the video call.",
    ],
    [
        "Sir mujhe bahut dar lag raha hai. Bataiye mujhe kya karna hai.",
        "Theek hai sir, main video call par hoon.",
    ],
    ["सर मुझे बहुत डर लग रहा है। बताइए मुझे क्या करना है।", "ठीक है सर, मैं वीडियो कॉल पर हूँ।"],
)
OFFICERS = ["Rajesh Sharma", "Vikram Rathore", "Anil Kapoor", "Sandeep Yadav", "Rakesh Verma",
            "Amit Chauhan", "Deepak Mishra"]  # fmt: skip
SCAM_STAGES = (S_INTRO, S_ALLEGE, S_ARREST, S_SECRET, S_URGENT, S_VERIFY, S_TRANSFER, S_FOLLOW)


# Impersonated authority -> pretexts that actually fit it
AUTH_PRETEXTS = {
    "trai": ["sim"],
    "customs": ["parcel"],
    "ncb": ["parcel"],
    "cbi": ["laundering", "parcel"],
    "ed": ["laundering"],
    "police": ["laundering", "sim", "parcel"],
}
SCAM_VIDEO_CHANNEL_P = (0.30, 0.30, 0.40)  # pstn, voip, video


def scam_call_chunks(
    rng: np.random.Generator,
    lang: str,
    name: str | None = None,
    mode: str | None = None,
) -> list[str]:
    """One scam call as chunks. ``mode``: "short" (first contact, 2-4 chunks), "full"
    (5-12 chunks, always reaches the safe-account demand) or None (25% short / 75% full)."""
    li = LANGS.index(lang)
    if mode is None:
        mode = "short" if rng.random() < 0.25 else "full"
    auth = str(rng.choice(sorted(AUTHORITIES)))
    pre = str(rng.choice(AUTH_PRETEXTS[auth]))
    # AUTHORITIES tuples are (english, devanagari, latin-script)
    auth_name = AUTHORITIES[auth][{0: 0, 1: 2, 2: 1}[li]]
    ctx = {
        "name": name or pick_name(rng, lang),
        "officer": str(rng.choice(OFFICERS)),
        "auth": auth_name,
        "pretext": PRETEXTS[pre][li],
        "case": f"{int(rng.integers(100, 999))}/{int(rng.integers(2021, 2027))}",
    }
    if mode == "short":
        stages = [0, 1] + ([2] if rng.random() < 0.4 else [])
    else:
        stages = [0, 1, 2, 3]
        stages += [4] if rng.random() < 0.7 else []
        stages += [5] if rng.random() < 0.7 else []
        stages += [6]
        stages += [7] if rng.random() < 0.5 else []
    chunks: list[str] = []
    for si in stages:
        variants = SCAM_STAGES[si][li]
        chunks.append(variants[int(rng.integers(len(variants)))].format(**ctx))
        if si in (1, 3, 4) and rng.random() < 0.6:  # victim interjection, no scam markers
            rep = VICTIM_REPLIES[li]
            chunks.append(rep[int(rng.integers(len(rep)))])
    return chunks


# ------------------------------------------------------------- benign scripts
BENIGN_KINDS = ("bank_care", "delivery", "family", "telemarketing")
BENIGN_P = (0.2, 0.2, 0.4, 0.2)
# per-kind channel mix (pstn, voip, video): family/WhatsApp calls are often video
BENIGN_CHANNEL_P = {
    "bank_care": (0.85, 0.10, 0.05),
    "delivery": (0.90, 0.10, 0.00),
    "family": (0.30, 0.30, 0.40),
    "telemarketing": (0.80, 0.15, 0.05),
}
NAMES = (
    ["Rahul Mehta", "Priya Nair", "Sunita Devi", "Arjun Reddy", "Kavita Joshi", "Mohammed Irfan",
     "Anjali Gupta", "Suresh Patil", "Neha Singh", "Ramesh Iyer", "Pooja Banerjee", "Imran Khan"],
    ["राहुल मेहता", "प्रिया नायर", "सुनीता देवी", "अर्जुन रेड्डी", "कविता जोशी", "मोहम्मद इरफान",
     "अंजलि गुप्ता", "सुरेश पाटिल", "नेहा सिंह", "रमेश अय्यर", "पूजा बनर्जी", "इमरान खान"],
)  # fmt: skip

# BENIGN[kind][lang_index] = (openings, bodies, closings), >= 6 variants each. Placeholders:
# {name}, {bank}, {shop}.
BENIGN: dict[str, list[tuple[list[str], list[str], list[str]]]] = {
    "bank_care": [
        (
            ["Hello, am I speaking with {name}? This is customer care from {bank} bank.",
             "Good morning {name}, {bank} bank calling about the service request you raised.",
             "Hi, this is {bank} bank. Is this a good time to talk for two minutes?",
             "Hello {name}, I am calling from {bank} bank regarding your credit card account.",
             "Good evening, {bank} bank relationship team here. May I speak with {name}?",
             "Hello, {bank} bank phone banking. You had called us about your debit card."],
            ["Your credit card statement was generated yesterday and the due amount is a few thousand rupees.",
             "The failed UPI transaction you complained about has been reversed to your account.",
             "For security, please confirm your date of birth. We will never ask for your OTP or PIN.",
             "Your new debit card has been dispatched and should reach you within five working days.",
             "A reminder that the RBI and the bank never ask customers to move money to a safe account, so ignore such calls and report them.",
             "Your KYC update is complete, no further documents are needed from your side.",
             "Your fixed deposit is maturing next month, you can renew it through the app or at the branch."],
            ["Thank you for banking with us. Have a nice day.",
             "Is there anything else I can help you with today?",
             "Thanks {name}, please rate this call after we disconnect.",
             "You can also visit your nearest branch if you prefer. Thank you.",
             "Your service request number will arrive by SMS. Goodbye.",
             "Thank you for your time, have a pleasant day."],
        ),
        (
            ["Hello, kya main {name} ji se baat kar raha hoon? {bank} bank customer care se bol raha hoon.",
             "Namaste {name}, {bank} bank se call hai, aapne jo service request daali thi uske baare me.",
             "Hi, {bank} bank se bol rahe hain. Do minute baat kar sakte hain?",
             "Hello {name} ji, aapke credit card account ke baare me {bank} bank se call hai.",
             "Good evening, {bank} bank ki relationship team se bol raha hoon. {name} ji se baat ho sakti hai?",
             "Hello, {bank} bank phone banking. Aapne debit card ke baare me call kiya tha."],
            ["Aapke credit card ka statement kal generate hua hai, due amount kuch hazaar rupaye hai.",
             "Aapki failed UPI transaction ka paisa account me wapas aa gaya hai.",
             "Suraksha ke liye apni date of birth confirm kijiye. Hum kabhi OTP ya PIN nahi maangte.",
             "Aapka naya debit card bhej diya gaya hai, paanch working days me pahunch jayega.",
             "Ek yaad dilana hai, bank ya RBI kabhi safe account me paise transfer karne ko nahi kehte, aisi calls ko ignore karke report kijiye.",
             "Aapka KYC update ho gaya hai, ab koi aur document nahi chahiye.",
             "Aapki fixed deposit agle mahine mature ho rahi hai, app ya branch se renew kar sakte hain."],
            ["Bank se jude rehne ke liye dhanyavaad. Aapka din shubh ho.",
             "Kuch aur madad chahiye aapko?",
             "Dhanyavaad {name} ji, call ke baad rating zaroor dijiye.",
             "Aap chahein to nazdeeki branch bhi aa sakte hain. Dhanyavaad.",
             "Service request number SMS se aa jayega. Namaste.",
             "Samay dene ke liye shukriya, aapka din achha ho."],
        ),
        (
            ["हेलो, क्या मैं {name} जी से बात कर रहा हूँ? मैं {bank} बैंक कस्टमर केयर से बोल रहा हूँ।",
             "नमस्ते {name}, {bank} बैंक से कॉल है, आपने जो सर्विस रिक्वेस्ट डाली थी उसके बारे में।",
             "हाय, {bank} बैंक से बोल रहे हैं। दो मिनट बात कर सकते हैं?",
             "हेलो {name} जी, आपके क्रेडिट कार्ड अकाउंट के बारे में {bank} बैंक से कॉल है।",
             "गुड इवनिंग, {bank} बैंक की रिलेशनशिप टीम से बोल रहा हूँ। क्या {name} जी से बात हो सकती है?",
             "हेलो, {bank} बैंक फोन बैंकिंग। आपने डेबिट कार्ड के बारे में कॉल किया था।"],
            ["आपके क्रेडिट कार्ड का स्टेटमेंट कल जनरेट हुआ है, बकाया कुछ हज़ार रुपये है।",
             "आपकी फेल हुई UPI ट्रांजैक्शन का पैसा आपके अकाउंट में वापस आ गया है।",
             "सुरक्षा के लिए अपनी जन्मतिथि कन्फर्म कीजिए। हम कभी OTP या PIN नहीं मांगते।",
             "आपका नया डेबिट कार्ड भेज दिया गया है, पाँच कार्य दिवस में पहुँच जाएगा।",
             "एक बात याद दिला दूँ, बैंक या RBI कभी सेफ अकाउंट में पैसे ट्रांसफर करने को नहीं कहते, ऐसी कॉल को अनदेखा करके रिपोर्ट कीजिए।",
             "आपका KYC अपडेट हो गया है, अब कोई और दस्तावेज़ नहीं चाहिए।",
             "आपकी फिक्स्ड डिपॉजिट अगले महीने मैच्योर हो रही है, ऐप या ब्रांच से रिन्यू कर सकते हैं।"],
            ["बैंक से जुड़े रहने के लिए धन्यवाद। आपका दिन शुभ हो।",
             "क्या मैं आपकी और कोई मदद कर सकता हूँ?",
             "धन्यवाद {name} जी, कॉल के बाद रेटिंग ज़रूर दीजिए।",
             "आप चाहें तो नज़दीकी ब्रांच भी आ सकते हैं। धन्यवाद।",
             "सर्विस रिक्वेस्ट नंबर SMS से आ जाएगा। नमस्ते।",
             "समय देने के लिए शुक्रिया, आपका दिन अच्छा हो।"],
        ),
    ],
    "delivery": [
        (
            ["Hello {name}, I am the delivery executive from {shop}. I am outside your gate.",
             "Hi sir, {shop} delivery here. Is this {name}?",
             "Good afternoon, calling from {shop}. Your parcel is out for delivery today.",
             "Hello, delivery boy from {shop}. I am near your society, which lane is it?",
             "Hi {name}, {shop} courier. I have a package for you but the address is unclear.",
             "Namaste, {shop} delivery partner here. I reached your building."],
            ["Which block is it? Can you come down or shall I leave it with the guard?",
             "Please keep the order number ready, it is in the app.",
             "It is cash on delivery, the amount is shown in the app.",
             "Will someone be at home between two and four pm?",
             "I can see the gate but not the flat number, could you share it?",
             "The lift is not working so I am coming up by the stairs.",
             "Your package is a bit large, I will need you to sign for it."],
            ["Delivered. Thank you, please give a good rating.",
             "Okay, I will hand it to the guard. Thanks.",
             "Thank you sir, have a good day.",
             "I will wait five minutes at the gate. Thanks.",
             "Done, please check the package and confirm in the app.",
             "Thanks {name}, bye."],
        ),
        (
            ["Hello {name} ji, main {shop} se delivery boy bol raha hoon. Aapke gate ke bahar hoon.",
             "Hi sir, {shop} delivery se bol raha hoon. {name} ji hain?",
             "Namaste, {shop} se call hai. Aapka parcel aaj deliver hone wala hai.",
             "Hello, {shop} ka delivery boy. Society ke paas hoon, kaunsi gali hai?",
             "Hi {name}, {shop} courier. Aapka package hai par address clear nahi hai.",
             "Namaste, {shop} delivery partner bol raha hoon. Aapki building pahunch gaya."],
            ["Kaunsa block hai? Aap neeche aayenge ya guard ko de doon?",
             "Order number ready rakhiye, app me dikh jayega.",
             "Cash on delivery hai, amount app me dikh raha hai.",
             "Do se chaar baje ke beech koi ghar par hoga?",
             "Gate dikh raha hai par flat number nahi, bata dijiye.",
             "Lift band hai to seedhiyon se aa raha hoon.",
             "Package thoda bada hai, aapko sign karna padega."],
            ["Deliver ho gaya. Dhanyavaad, achhi rating dena.",
             "Theek hai guard ko de deta hoon. Shukriya.",
             "Dhanyavaad sir, aapka din achha ho.",
             "Paanch minute gate par ruk raha hoon. Dhanyavaad.",
             "Ho gaya, package check karke app me confirm kar dijiye.",
             "Shukriya {name} ji, bye."],
        ),
        (
            ["हेलो {name} जी, मैं {shop} से डिलीवरी बॉय बोल रहा हूँ। आपके गेट के बाहर हूँ।",
             "हाय सर, {shop} डिलीवरी से बोल रहा हूँ। {name} जी हैं?",
             "नमस्ते, {shop} से कॉल है। आपका पार्सल आज डिलीवर होने वाला है।",
             "हेलो, {shop} का डिलीवरी बॉय। सोसाइटी के पास हूँ, कौन सी गली है?",
             "हाय {name}, {shop} कूरियर। आपका पैकेज है पर पता साफ़ नहीं है।",
             "नमस्ते, {shop} डिलीवरी पार्टनर बोल रहा हूँ। आपकी बिल्डिंग पहुँच गया।"],
            ["कौन सा ब्लॉक है? आप नीचे आएंगे या गार्ड को दे दूँ?",
             "ऑर्डर नंबर तैयार रखिए, ऐप में दिख जाएगा।",
             "कैश ऑन डिलीवरी है, रकम ऐप में दिख रही है।",
             "दो से चार बजे के बीच कोई घर पर होगा?",
             "गेट दिख रहा है पर फ्लैट नंबर नहीं, बता दीजिए।",
             "लिफ्ट बंद है तो सीढ़ियों से आ रहा हूँ।",
             "पैकेज थोड़ा बड़ा है, आपको साइन करना पड़ेगा।"],
            ["डिलीवर हो गया। धन्यवाद, अच्छी रेटिंग देना।",
             "ठीक है गार्ड को दे देता हूँ। शुक्रिया।",
             "धन्यवाद सर, आपका दिन अच्छा हो।",
             "पाँच मिनट गेट पर रुक रहा हूँ। धन्यवाद।",
             "हो गया, पैकेज चेक करके ऐप में कन्फर्म कर दीजिए।",
             "शुक्रिया {name} जी, बाय।"],
        ),
    ],
    "family": [
        (
            ["Hi {name}, did you have lunch? I was just thinking about you.",
             "Hey {name}, are you free for a minute? Wanted to catch up.",
             "Hello {name}! Long time, how have you been?",
             "Hi {name}, can you see me? The video is a bit laggy on my side.",
             "Hey {name}, happy birthday! Hope you are having a great day.",
             "Hi {name}, just calling to check you reached home safely."],
            ["Everything is fine here. Dad went for his evening walk.",
             "Come home this weekend, I will make your favourite paneer.",
             "Are we still on for the cricket match on Sunday? I will book the ground.",
             "The wedding is on the fifteenth, you must come a day early to help.",
             "Don't work too much, you sound tired. Are you eating properly?",
             "I sent you the photos from the trip on WhatsApp, did you get them?",
             "Grandma was asking about you, she wants to talk to you on video."],
            ["Okay, take care. Call me when you reach. Bye.",
             "See you on Sunday then. Bye!",
             "Love you, talk tomorrow. Bye.",
             "Okay okay, I will call you later, the network is bad here.",
             "Give my regards to everyone at home. Bye {name}.",
             "Alright, goodnight. Sleep well."],
        ),
        (
            ["Hello {name}, khana khaya? Bas tumhari yaad aa rahi thi.",
             "Arre {name}, ek minute free ho? Baat karni thi.",
             "Hello {name}! Kaafi time baad, kaise ho?",
             "Hi {name}, mujhe dikh rahe ho? Meri taraf video thoda atak raha hai.",
             "Arre {name}, janamdin mubarak! Din badhiya ja raha hoga.",
             "Hi {name}, bas check karne ko call kiya ki ghar pahunch gaye ya nahi."],
            ["Yahan sab theek hai. Papa shaam ki sair par gaye hain.",
             "Is weekend ghar aa jao, tumhari pasand ka paneer banaungi.",
             "Sunday ko cricket match pakka hai na? Main ground book kar leta hoon.",
             "Shaadi pandrah tarikh ko hai, ek din pehle aa jana madad ke liye.",
             "Zyada kaam mat karna, thake hue lag rahe ho. Theek se khana kha rahe ho?",
             "Trip ki photos WhatsApp par bheji hain, mili kya?",
             "Naani tumhare baare me pooch rahi thi, video par baat karna chahti hain."],
            ["Achha apna dhyan rakhna. Pahunch kar phone karna. Bye.",
             "To Sunday ko milte hain. Bye!",
             "Love you, kal baat karte hain. Bye.",
             "Achha achha, baad me call karta hoon, yahan network kharab hai.",
             "Sabko mera namaste kehna. Bye {name}.",
             "Theek hai, shubh ratri. Aaram se sona."],
        ),
        (
            ["हेलो {name}, खाना खाया? बस तुम्हारी याद आ रही थी।",
             "अरे {name}, एक मिनट फ्री हो? बात करनी थी।",
             "हेलो {name}! काफ़ी समय बाद, कैसे हो?",
             "हाय {name}, मैं दिख रहा हूँ? मेरी तरफ़ वीडियो थोड़ा अटक रहा है।",
             "अरे {name}, जन्मदिन मुबारक! दिन बढ़िया जा रहा होगा।",
             "हाय {name}, बस चेक करने के लिए कॉल किया कि घर पहुँच गए या नहीं।"],
            ["यहाँ सब ठीक है। पापा शाम की सैर पर गए हैं।",
             "इस वीकेंड घर आ जाओ, तुम्हारी पसंद का पनीर बनाऊंगी।",
             "रविवार को क्रिकेट मैच पक्का है ना? मैं ग्राउंड बुक कर लेता हूँ।",
             "शादी पंद्रह तारीख को है, एक दिन पहले आ जाना मदद के लिए।",
             "ज़्यादा काम मत करना, थके हुए लग रहे हो। ठीक से खाना खा रहे हो?",
             "ट्रिप की फ़ोटो WhatsApp पर भेजी हैं, मिलीं क्या?",
             "नानी तुम्हारे बारे में पूछ रही थीं, वीडियो पर बात करना चाहती हैं।"],
            ["अच्छा अपना ध्यान रखना। पहुँचकर फोन करना। बाय।",
             "तो रविवार को मिलते हैं। बाय!",
             "लव यू, कल बात करते हैं। बाय।",
             "अच्छा अच्छा, बाद में कॉल करता हूँ, यहाँ नेटवर्क खराब है।",
             "सबको मेरा नमस्ते कहना। बाय {name}।",
             "ठीक है, शुभ रात्रि। आराम से सोना।"],
        ),
    ],
    "telemarketing": [
        (
            ["Good afternoon {name}, I am calling from {shop} finance.",
             "Hello sir, this is a quick call from {shop} about an offer for you.",
             "Hi {name}, am I speaking with the right person? {shop} here.",
             "Good morning madam, {shop} services calling. Do you have two minutes?",
             "Hello {name}, I am from the {shop} sales team.",
             "Hi, this is {shop}. You recently enquired on our website, so I am following up."],
            ["You are eligible for a pre-approved personal loan at a low interest rate with no processing fee this month.",
             "We have a new health insurance plan with cashless treatment in over ten thousand hospitals.",
             "We also have a free credit card with cashback on groceries and fuel.",
             "You can upgrade your plan today and get three months of the premium service free.",
             "The offer is valid till the end of this month and the paperwork is fully online.",
             "I can send you the brochure and the details on WhatsApp if you like.",
             "Our relationship manager can visit your home at a time convenient to you."],
            ["No problem, if you are not interested I will not call again. Thank you for your time.",
             "Okay, I will send the details on WhatsApp. Have a good day.",
             "Thank you {name}, you can reach us on the toll free number any time.",
             "Sure, I will call you back tomorrow evening. Thanks.",
             "Alright, sorry to disturb you. Goodbye.",
             "Thanks for your time sir, have a great day."],
        ),
        (
            ["Namaste {name} ji, main {shop} finance se bol raha hoon.",
             "Hello sir, {shop} se ek chhota sa call hai aapke liye offer ke baare me.",
             "Hi {name} ji, kya sahi vyakti se baat ho rahi hai? {shop} se bol rahe hain.",
             "Good morning madam, {shop} services se call hai. Do minute milenge?",
             "Hello {name}, main {shop} sales team se hoon.",
             "Hi, {shop} se bol raha hoon. Aapne hamari website par enquiry ki thi, usi ke follow up me call hai."],
            ["Aap pre-approved personal loan ke liye eligible hain, kam byaaj dar par aur is mahine koi processing fee nahi.",
             "Hamara naya health insurance plan hai jisme das hazaar se zyada hospitals me cashless ilaaj milta hai.",
             "Humare paas grocery aur fuel par cashback wala free credit card bhi hai.",
             "Aaj plan upgrade karenge to teen mahine premium service free milegi.",
             "Offer is mahine ke ant tak valid hai aur paperwork poora online hai.",
             "Agar chahein to main brochure aur details WhatsApp par bhej deta hoon.",
             "Hamare relationship manager aapke sahuliyat ke samay ghar aa sakte hain."],
            ["Koi baat nahi, interested nahi hain to dobara call nahi karunga. Samay dene ke liye dhanyavaad.",
             "Theek hai, main details WhatsApp par bhej deta hoon. Aapka din achha ho.",
             "Dhanyavaad {name} ji, aap kabhi bhi toll free number par sampark kar sakte hain.",
             "Zaroor, main kal shaam dobara call karunga. Shukriya.",
             "Theek hai, disturb karne ke liye maafi. Namaste.",
             "Samay dene ke liye dhanyavaad sir, aapka din shubh ho."],
        ),
        (
            ["नमस्ते {name} जी, मैं {shop} फाइनेंस से बोल रहा हूँ।",
             "हेलो सर, {shop} से एक छोटा सा कॉल है आपके लिए ऑफर के बारे में।",
             "हाय {name} जी, क्या सही व्यक्ति से बात हो रही है? {shop} से बोल रहे हैं।",
             "गुड मॉर्निंग मैडम, {shop} सर्विसेज़ से कॉल है। दो मिनट मिलेंगे?",
             "हेलो {name}, मैं {shop} सेल्स टीम से हूँ।",
             "हाय, {shop} से बोल रहा हूँ। आपने हमारी वेबसाइट पर पूछताछ की थी, उसी के फॉलो अप में कॉल है।"],
            ["आप प्री-अप्रूव्ड पर्सनल लोन के लिए पात्र हैं, कम ब्याज दर पर और इस महीने कोई प्रोसेसिंग फीस नहीं।",
             "हमारा नया हेल्थ इंश्योरेंस प्लान है जिसमें दस हज़ार से ज़्यादा अस्पतालों में कैशलेस इलाज मिलता है।",
             "हमारे पास किराना और फ्यूल पर कैशबैक वाला फ्री क्रेडिट कार्ड भी है।",
             "आज प्लान अपग्रेड करेंगे तो तीन महीने प्रीमियम सर्विस फ्री मिलेगी।",
             "ऑफर इस महीने के अंत तक वैलिड है और पेपरवर्क पूरा ऑनलाइन है।",
             "अगर चाहें तो मैं ब्रोशर और डिटेल्स WhatsApp पर भेज देता हूँ।",
             "हमारे रिलेशनशिप मैनेजर आपके सुविधाजनक समय पर घर आ सकते हैं।"],
            ["कोई बात नहीं, इच्छुक नहीं हैं तो दोबारा कॉल नहीं करूंगा। समय देने के लिए धन्यवाद।",
             "ठीक है, मैं डिटेल्स WhatsApp पर भेज देता हूँ। आपका दिन अच्छा हो।",
             "धन्यवाद {name} जी, आप कभी भी टोल फ्री नंबर पर संपर्क कर सकते हैं।",
             "ज़रूर, मैं कल शाम दोबारा कॉल करूंगा। शुक्रिया।",
             "ठीक है, परेशान करने के लिए माफ़ी। नमस्ते।",
             "समय देने के लिए धन्यवाद सर, आपका दिन शुभ हो।"],
        ),
    ],
}  # fmt: skip
FILLERS = (
    ["Okay.", "Yes, go on.", "Sorry, can you repeat that?", "Hmm, I see.", "Right, understood.",
     "One second, let me check.", "Yes yes, that is fine.", "Hello? Can you hear me?"],
    ["Achha.", "Haan, boliye.", "Sorry, dobara bolenge?", "Hmm, samajh gaya.", "Theek hai.",
     "Ek second, check karta hoon.", "Haan haan, chalega.", "Hello? Awaaz aa rahi hai?"],
    ["अच्छा।", "हाँ, बोलिए।", "सॉरी, दोबारा बोलेंगे?", "हम्म, समझ गया।", "ठीक है।",
     "एक सेकंड, चेक करता हूँ।", "हाँ हाँ, चलेगा।", "हेलो? आवाज़ आ रही है?"],
)  # fmt: skip
BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak"]
SHOPS = ["Amazon", "Flipkart", "Swiggy", "Zomato", "BlueDart", "Bajaj", "Policybazaar"]


def pick_name(rng: np.random.Generator, lang: str) -> str:
    pool = NAMES[1] if lang == "hi" else NAMES[0]
    return pool[int(rng.integers(len(pool)))]


def benign_call_chunks(rng: np.random.Generator, kind: str, lang: str) -> list[str]:
    li = LANGS.index(lang)
    opens, bodies, closes = BENIGN[kind][li]
    ctx = {
        "name": pick_name(rng, lang),
        "bank": str(rng.choice(BANKS)),
        "shop": str(rng.choice(SHOPS)),
    }
    n_body = int(rng.choice([1, 2, 2, 3, 3, 4]))
    body_idx = rng.permutation(len(bodies))[:n_body]
    chunks = [opens[int(rng.integers(len(opens)))]]
    chunks += [bodies[int(i)] for i in body_idx]
    n_fill = int(rng.choice([0, 0, 1, 1, 2, 3, 5]))
    for _ in range(n_fill):  # filler back-and-forth makes some benign calls long
        chunks.insert(int(rng.integers(1, len(chunks) + 1)), FILLERS[li][int(rng.integers(8))])
    chunks.append(closes[int(rng.integers(len(closes)))])
    return [c.format(**ctx) for c in chunks]


def _pick_lang(rng: np.random.Generator) -> str:
    return str(rng.choice(LANGS, p=LANG_P))


# ------------------------------------------------------ labelled corpora (training)
def scam_call_corpus(seed: int, n: int) -> list[tuple[str, str, str]]:
    """(full call text, scam authority-pretext kind, lang); all label = scam."""
    rng = np.random.default_rng([seed, 31])
    out = []
    for _ in range(n):
        lang = _pick_lang(rng)
        out.append((" ".join(scam_call_chunks(rng, lang)), "digital_arrest", lang))
    return out


def benign_call_corpus(seed: int, n: int) -> list[tuple[str, str, str]]:
    """(full call text, benign kind, lang); all label = benign."""
    rng = np.random.default_rng([seed, 32])
    out = []
    for _ in range(n):
        kind = str(rng.choice(BENIGN_KINDS, p=BENIGN_P))
        lang = _pick_lang(rng)
        out.append((" ".join(benign_call_chunks(rng, kind, lang)), kind, lang))
    return out


def chunks_to_events(
    rng: np.random.Generator,
    call_key: str,
    victim_token: str,
    caller_hash: str,
    start: datetime,
    chunks: list[str],
    lang: str,
    channel: str,
    gap_s: tuple[float, float] = (20.0, 70.0),
) -> list[CallEvent]:
    call_id = stable_id("call", call_key)
    ts = start
    events = []
    for i, text in enumerate(chunks):
        events.append(
            CallEvent(
                call_id=call_id,
                idempotency_key=f"{call_id}:{i}",
                victim_token=victim_token,
                caller_number_hash=caller_hash,
                ts=ts,
                transcript_chunk=text,
                channel=channel,  # type: ignore[arg-type]
                lang=lang,
            )
        )
        ts = ts + timedelta(seconds=float(rng.uniform(*gap_s)))
    return events


def gen_benign_calls(
    world: World, days: int, seed: int, rate_per_citizen_per_day: float = 0.15
) -> Iterator[CallEvent]:
    """Benign calls (bank care, delivery, family, telemarketing), chronological."""
    rng = np.random.default_rng([seed, 33])
    cits = world.citizens
    all_events: list[CallEvent] = []
    idx = 0
    for d in range(days):
        n = int(rng.poisson(len(cits) * rate_per_citizen_per_day))
        for _ in range(n):
            cit = cits[int(rng.integers(len(cits)))]
            kind = str(rng.choice(BENIGN_KINDS, p=BENIGN_P))
            lang = _pick_lang(rng)
            hour = float(rng.uniform(8, 21))
            start = world.start + timedelta(days=d, hours=hour)
            channel = str(rng.choice(["pstn", "voip", "video"], p=BENIGN_CHANNEL_P[kind]))
            number = f"+91{int(rng.integers(6_000_000_000, 9_999_999_999))}"
            all_events.extend(
                chunks_to_events(
                    rng, f"b:{world.seed}:{seed}:{idx}",
                    world.payer_token(cit.citizen_id), world.phone_hash(number), start,
                    benign_call_chunks(rng, kind, lang), lang, channel,
                )
            )  # fmt: skip
            idx += 1
    all_events.sort(key=lambda e: e.ts)
    yield from all_events
