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
        "Madam/sir, our investigation shows that {pretext}. This is a criminal offence under "
        "the PMLA and the NDPS Act.",
        "We have received a complaint and evidence: {pretext}. Your name is in the FIR.",
    ],
    [
        "Sir/madam, hamari jaanch me saamne aaya hai ki {pretext}. Yeh PMLA aur NDPS act ke "
        "tahat criminal offence hai.",
        "Hamare paas complaint aur saboot hai: {pretext}. FIR me aapka naam hai.",
    ],
    [
        "सर/मैडम, हमारी जांच में सामने आया है कि {pretext}। यह PMLA और NDPS एक्ट के तहत आपराधिक अपराध है।",
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
    ],  # noqa: E501
    ["सर मुझे बहुत डर लग रहा है। बताइए मुझे क्या करना है।", "ठीक है सर, मैं वीडियो कॉल पर हूँ।"],
)
OFFICERS = ["Rajesh Sharma", "Vikram Rathore", "Anil Kapoor", "Sandeep Yadav", "Rakesh Verma",
            "Amit Chauhan", "Deepak Mishra"]  # fmt: skip
SCAM_STAGES = (S_INTRO, S_ALLEGE, S_ARREST, S_SECRET, S_URGENT, S_VERIFY, S_TRANSFER, S_FOLLOW)


def scam_call_chunks(rng: np.random.Generator, lang: str, name: str = "Sir/Madam") -> list[str]:
    li = LANGS.index(lang)
    auth = str(rng.choice(sorted(AUTHORITIES)))
    pre = str(rng.choice(sorted(PRETEXTS)))
    # AUTHORITIES tuples are (english, devanagari, latin-script)
    auth_name = AUTHORITIES[auth][{0: 0, 1: 2, 2: 1}[li]]
    ctx = {
        "name": name,
        "officer": str(rng.choice(OFFICERS)),
        "auth": auth_name,
        "pretext": PRETEXTS[pre][li],
        "case": f"{int(rng.integers(100, 999))}/{int(rng.integers(2021, 2027))}",
    }
    chunks: list[str] = []
    for si, stage in enumerate(SCAM_STAGES):
        variants = stage[li]
        chunks.append(variants[int(rng.integers(len(variants)))].format(**ctx))
        if si in (1, 3, 4) and rng.random() < 0.7:  # victim interjection, no scam markers
            rep = VICTIM_REPLIES[li]
            chunks.append(rep[int(rng.integers(len(rep)))])
    return chunks


# ------------------------------------------------------------- benign scripts
BENIGN_KINDS = ("bank_care", "delivery", "family", "telemarketing")
BENIGN_P = (0.25, 0.25, 0.3, 0.2)

BENIGN_SCRIPTS: dict[str, tuple[list[list[str]], list[list[str]], list[list[str]]]] = {
    "bank_care": (
        [
            ["Hello, this is customer care from {bank} bank. We are calling about your credit "
             "card statement that was generated yesterday.",
             "Sir, for security please confirm your date of birth. Remember, we will never ask "
             "for your OTP or PIN.",
             "Your due amount is a few thousand rupees, payable by the due date. You can pay "
             "through the app or net banking at your convenience.",
             "Thank you for banking with us. Have a nice day."],
            ["Good morning, {bank} bank here. You raised a complaint about a failed UPI "
             "transaction. The amount has been reversed to your account.",
             "Please check your passbook. Is there anything else I can help you with?",
             "Also, for your safety: the RBI and the bank never ask customers to move money to a "
             "safe account, so ignore such calls and report them.",
             "Thank you, please rate this call after we disconnect."],
        ],
        [
            ["Hello, main {bank} bank customer care se bol raha hoon. Aapke credit card ka "
             "statement kal generate hua hai.",
             "Sir suraksha ke liye apni date of birth confirm kijiye. Yaad rakhiye hum kabhi OTP ya "
             "PIN nahi maangte.",
             "Aapka due amount kuch hazaar rupaye hai, due date tak bharna hai. App ya net banking "
             "se aaram se pay kar sakte hain.",
             "Bank se jude rehne ke liye dhanyavaad. Aapka din shubh ho."],
            ["Namaste, {bank} bank se bol rahe hain. Aapne failed UPI transaction ki complaint ki "
             "thi. Paisa aapke account me wapas aa gaya hai.",
             "Kripya passbook check kijiye. Kuch aur madad chahiye?",
             "Aur ek zaroori baat, bank ya RBI kabhi safe account me paise transfer karne ko nahi "
             "kehte, aisi calls ko ignore karke report kijiye.",
             "Dhanyavaad, call ke baad rating zaroor dijiye."],
        ],
        [
            ["हेलो, मैं {bank} बैंक कस्टमर केयर से बोल रहा हूँ। आपके क्रेडिट कार्ड का स्टेटमेंट कल "
             "जनरेट हुआ है।",
             "सर सुरक्षा के लिए अपनी जन्मतिथि कन्फर्म कीजिए। याद रखिए हम कभी OTP या PIN नहीं "
             "मांगते।",
             "आपका बकाया कुछ हज़ार रुपये है, तारीख तक भरना है। ऐप या नेट बैंकिंग से आराम से भर सकते हैं।",
             "बैंक से जुड़े रहने के लिए धन्यवाद। आपका दिन शुभ हो।"],
        ],
    ),
    "delivery": (
        [
            ["Hello sir, I am the delivery executive from {shop}. I am outside your society gate "
             "with your order.",
             "Which block is it? Can you come down or shall I send it up with the guard?",
             "Okay, I will hand it over to the guard. Please pay cash on delivery, the amount is "
             "in the app.",
             "Thank you sir, please give a good rating."],
            ["Hi, this is {shop} delivery. Your parcel is out for delivery today between two and "
             "four pm. Will someone be at home?",
             "Okay, I will call again when I reach the lane. Please keep the order number ready.",
             "Delivered, sir. Thank you."],
        ],
        [
            ["Hello sir, main {shop} se delivery boy bol raha hoon. Aapka order lekar society ke "
             "gate par khada hoon.",
             "Kaunsa block hai? Aap neeche aayenge ya guard ko de doon?",
             "Theek hai guard ko de deta hoon. Cash on delivery hai to payment app me dikh raha hai.",
             "Dhanyavaad sir, achhi rating dena."],
            ["Hi, {shop} delivery se bol raha hoon. Aapka parcel aaj do se chaar baje ke beech "
             "pahunchega. Koi ghar par hoga?",
             "Theek hai, gali me pahunch kar dobara call karunga. Order number ready rakhiye.",
             "Deliver ho gaya sir. Dhanyavaad."],
        ],
        [
            ["हेलो सर, मैं {shop} से डिलीवरी बॉय बोल रहा हूँ। आपका ऑर्डर लेकर सोसाइटी के गेट पर खड़ा हूँ।",
             "कौन सा ब्लॉक है? आप नीचे आएंगे या गार्ड को दे दूँ?",
             "ठीक है गार्ड को दे देता हूँ। कैश ऑन डिलीवरी है तो पेमेंट ऐप में दिख रहा है।",
             "धन्यवाद सर, अच्छी रेटिंग देना।"],
        ],
    ),
    "family": (
        [
            ["Hi beta, did you have lunch? I was just thinking about you.",
             "Yes yes, everything is fine here. Your father went for his evening walk.",
             "Come home this weekend, I will make your favourite paneer. Don't work too much.",
             "Okay, take care. Call me when you reach. Bye."],
            ["Hey bhai, are we still on for the match on Sunday?",
             "Great, I will book the ground. Bring the bats this time, no excuses!",
             "Cool, see you at six. Bye."],
        ],
        [
            ["Hello beta, khana khaya? Bas tumhari yaad aa rahi thi.",
             "Haan haan, yahan sab theek hai. Papa shaam ki sair par gaye hain.",
             "Is weekend ghar aa jao, tumhari pasand ka paneer banaungi. Zyada kaam mat karna.",
             "Achha apna dhyan rakhna. Pahunch kar phone karna. Bye."],
            ["Arre bhai, Sunday ko match pakka hai na?",
             "Badhiya, main ground book kar leta hoon. Is baar bat le aana, koi bahana nahi!",
             "Theek hai, chhe baje milte hain. Bye."],
        ],
        [
            ["हेलो बेटा, खाना खाया? बस तुम्हारी याद आ रही थी।",
             "हाँ हाँ, यहाँ सब ठीक है। पापा शाम की सैर पर गए हैं।",
             "इस वीकेंड घर आ जाओ, तुम्हारी पसंद का पनीर बनाऊंगी। ज़्यादा काम मत करना।",
             "अच्छा अपना ध्यान रखना। पहुँचकर फोन करना। बाय।"],
        ],
    ),
    "telemarketing": (
        [
            ["Good afternoon sir, I am calling from {shop} finance. You are eligible for a "
             "pre-approved personal loan at a low interest rate.",
             "There is no processing fee this month. May I explain the plan to you for two minutes?",
             "No problem sir, if you are not interested I will not call again. Thank you for your time."],
            ["Hello madam, this is a call about our new health insurance plan with cashless "
             "treatment in over ten thousand hospitals.",
             "We also have a free credit card with cashback on groceries. Would you like to "
             "know more?",
             "Okay, I will send the details on WhatsApp. Have a good day."],
        ],
        [
            ["Namaste sir, main {shop} finance se bol raha hoon. Aap pre-approved personal loan ke "
             "liye eligible hain, kam byaaj dar par.",
             "Is mahine processing fee nahi hai. Kya main do minute me plan samjha sakta hoon?",
             "Koi baat nahi sir, interested nahi hain to dobara call nahi karunga. Samay dene ke "
             "liye dhanyavaad."],
            ["Hello madam, hamare naye health insurance plan ke baare me call hai, das hazaar se "
             "zyada hospitals me cashless ilaaj.",
             "Humare paas grocery par cashback wala free credit card bhi hai. Kya aap jaanna "
             "chahengi?",
             "Theek hai, main details WhatsApp par bhej deta hoon. Aapka din achha ho."],
        ],
        [
            ["नमस्ते सर, मैं {shop} फाइनेंस से बोल रहा हूँ। आप कम ब्याज दर पर पर्सनल लोन के लिए "
             "प्री-अप्रूव्ड हैं।",
             "इस महीने प्रोसेसिंग फीस नहीं है। क्या मैं दो मिनट में प्लान समझा सकता हूँ?",
             "कोई बात नहीं सर, इच्छुक नहीं हैं तो दोबारा कॉल नहीं करूंगा। समय देने के लिए धन्यवाद।"],
        ],
    ),
}  # fmt: skip
BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak"]
SHOPS = ["Amazon", "Flipkart", "Swiggy", "Zomato", "BlueDart", "Bajaj", "Policybazaar"]


def benign_call_chunks(rng: np.random.Generator, kind: str, lang: str) -> list[str]:
    li = LANGS.index(lang)
    variants = BENIGN_SCRIPTS[kind][li]
    script = variants[int(rng.integers(len(variants)))]
    ctx = {"bank": str(rng.choice(BANKS)), "shop": str(rng.choice(SHOPS))}
    return [c.format(**ctx) for c in script]


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
    world: World,
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
            channel = "pstn" if rng.random() < 0.7 else "voip"
            number = f"+91{int(rng.integers(6_000_000_000, 9_999_999_999))}"
            all_events.extend(
                chunks_to_events(
                    world, rng, f"b:{world.seed}:{seed}:{idx}",
                    world.payer_token(cit.citizen_id), world.phone_hash(number), start,
                    benign_call_chunks(rng, kind, lang), lang, channel,
                )
            )  # fmt: skip
            idx += 1
    all_events.sort(key=lambda e: e.ts)
    yield from all_events
