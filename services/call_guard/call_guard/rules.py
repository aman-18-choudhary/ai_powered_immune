"""Rule layer (``rules-v1``): multilingual cue detection for digital-arrest scam calls.

Handles English, romanised Hindi and Devanagari Hindi.

How a score is produced
-----------------------
1. Text is normalised (NFKC, zero-width characters removed) and split into sentences, then into
   clauses (on ``, ; - : but however lekin magar``).
2. A clause that is *advisory* (scam-awareness: "we will never ask for your OTP", "ignore such
   calls", helpline notices, TV/web-series remarks, "if you receive a call claiming ...") is
   dropped before cues are looked for. Advisory patterns are specific phrases, not single words,
   and only the advisory clause is dropped: "We will never ask for money but you must transfer
   your funds to the RBI safe account" still fires on its second clause. Demand cues
   (ISOLATION_DEMAND, SAFE_ACCOUNT_TRANSFER, PAYMENT_DEMAND, DIGITAL_ARREST_PHRASE) are written
   as imperative / second-person patterns, so a bare topical mention ("digital arrest scams",
   "safe accounts") never matches them.
3. Cue classes found in the chunk are combined by noisy-OR: ``1 - prod(1 - w)``.

Weights (every single class is deliberately below the 0.7 call-risk threshold, so crossing
needs at least two independent classes, e.g. authority claim + allegation / isolation / transfer)::

    DIGITAL_ARREST_PHRASE   0.69  "you are under digital arrest" (second person/accusation only)
    SAFE_ACCOUNT_TRANSFER   0.69  move funds to a safe / RBI / verification account
    ISOLATION_DEMAND        0.55  one of: stay on call, tell no one, camera/room confinement
                            0.75  two or more of those sub-types together (independent cues)
    PAYMENT_DEMAND          0.50  send money / deposit in the same clause as arrest, jail, case
    AUTHORITY_IMPERSONATION 0.40  authority name + first-person / official-call framing
    LEGAL_THREAT            0.55  FIR, warrant, PMLA/NDPS, laundering, seized parcel / drugs,
                                  Aadhaar or SIM linked to illegal activity
                            0.30  weak: arrest / jail / number-blocking mentions
    ACCOUNT_VERIFICATION_ASK 0.45 verify account, show balance / banking app, share Aadhaar+PAN
    CREDENTIAL_ASK          0.35  share / tell / send your OTP, PIN, CVV or password
    URGENCY                 0.15  time pressure

Meaning of the score: see ``call_guard.model`` (a blend of these heuristics with a calibrated
classifier; it is not a real-world probability).
"""

import re
import unicodedata
from functools import reduce

from scam_contracts.models import CallEvent, Reason

RULES_VERSION = "rules-v1"
CALL_RISK_THRESHOLD = 0.7

_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E], None)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(_norm("|".join(alts)), re.IGNORECASE)


_SENT_SPLIT = re.compile(r"(?<=[.?!।])\s+|\n+")
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:[,;:—–]|\s-\s|\bbut\b|\bhowever\b|\blekin\b|\bmagar\b|\bparantu\b|\bkintu\b"
    r"|लेकिन|मगर|परंतु|किंतु)\s*",
    re.IGNORECASE,
)

# Advisory clauses (awareness / media / helpline). Specific phrases only.
_ADVISORY = _rx(
    r"\bnever (?:ask|asks|asked|request|requests|demand|demands|require|want|call you|share|give)\b",
    r"\b(?:will not|won't|would not|do not|don't|never) (?:ask|request|demand) (?:for )?(?:your )?",
    r"\b(?:do not|don't|never|should not|must not) (?:share|give|reveal|disclose) (?:your |the )?(?:otp|pin|cvv|password|net ?banking)",
    r"\bignore (?:such|these|those|any|all|calls|messages|the calls)\b",
    r"\bbeware\b",
    r"\bbe (?:careful|alert|aware|vigilant)\b",
    r"\bstay (?:alert|vigilant)\b",
    r"\bfraud (?:awareness|alert|prevention)\b",
    r"\bscam (?:alert|awareness)\b",
    r"\bawareness\b",
    r"\b(?:is|are|it is|these are) (?:a |an )?(?:scam|scams|fraud(?! case)|frauds|fake|not real|a hoax)\b",
    r"\bscams?\b.{0,40}\b(?:pose|posing|pretend|claim|threaten)",
    r"\bscammers\b",
    r"\bif you (?:receive|get|are called)\b",
    r"\bclaiming (?:you|that you)\b",
    r"\b1930\b",
    r"\bhelpline\b",
    r"\bno (?:agency|bank|official|officer|bank staff)\b.{0,40}\b(?:will|can|ever|does)\b",
    r"\bbank staff will never\b",
    r"\b(?:report|reporting) (?:such|it|them|these|any)\b",
    r"\bno (?:risk|arrest|chance|danger)\b",
    r"\bnot (?:real|true)\b",
    r"\b(?:simply |just )?hang up\b",
    r"\bdisconnect and (?:dial|call|report)\b",
    r"\bdocumentary\b",
    r"\bepisode\b",
    r"\bweb series\b",
    r"\bserial\b",
    r"\bmovie\b",
    r"\bfilm\b",
    r"\bnews (?:channel|report|article)\b",
    r"\bi read an article\b",
    r"\barticle about\b",
    r"\bhero gets\b",
    r"\btv (?:show|series|par|pe)\b",
    r"\bon tv\b",
    r"\bwatched\b",
    r"\bkabhi\b.{0,40}\b(?:nahi|na)\b.{0,20}\b(?:mang|kah|keh|puch|maang)",
    r"\b(?:nahi|na) (?:mangta|maangta|mangte|maangte|kehta|kehte|kahta|kahte)\b",
    r"\bsavdhan\b",
    r"\bsatark\b",
    r"\bchetavni\b",
    r"\bsuchna\b",
    r"\bjhooth hai\b",
    r"\bscam hai\b",
    r"\bdhokha(?:dhadi)?\b",
    r"\bkoi bhi (?:agency|bank|afsar|officer)\b",
    r"\bdikhaya\b",
    r"\bserial me\b",
    r"\btv par\b",
    r"\bdocumentary\b",
    r"\bpadha (?:ki|hai)\b",
    r"\bshare (?:mat|na) (?:kijiye|karein|karen|kare)\b",
    r"\bignore karke\b",
    r"\bignore kijiye\b",
    r"कभी.{0,40}(?:नहीं|न)\s*(?:मांगत|कहत|पूछत)",
    r"(?:नहीं|न)\s*(?:मांगता|मांगते|कहता|कहते)",
    r"अनदेखा",
    r"सावधान",
    r"सतर्क",
    r"चेतावनी",
    r"सूचना:",
    r"झूठ है",
    r"स्कैम",
    r"धोखाधड़ी से बच",
    r"कोई भी (?:एजेंसी|बैंक|अधिकारी|कर्मचारी)",
    r"टीवी",
    r"सीरियल",
    r"डॉक्यूमेंट्री",
    r"वेब सीरीज़?",
    r"शेयर (?:न|मत) (?:करें|कीजिए)",
    r"रिपोर्ट (?:करें|कीजिए)",
    r"पढ़ा (?:कि|है)",
    r"दिखाया",
    r"\b(?:delivery|courier|package|parcel)\b.{0,80}\botp\b",
    r"\botp\b.{0,80}\b(?:delivery|courier|package|darwaze|door)\b",
)

# Sentence-level benign context (a police-station visit / complaint is about the speaker's own matter).
_BENIGN_SENT = _rx(
    # own police-station visit / complaint
    r"\bpolice station\b",
    r"\bfiled a complaint\b",
    r"\blost (?:my )?phone\b",
    r"पुलिस स्टेशन",
    r"थाने",
    r"\bmaine\b.{0,40}\bcomplaint\b",
    r"\bthane\b",
    r"\b(?:mummy|papa|mom|dad|mama|nani|dadi)\b ko\b",
    # media / news / fiction narration
    r"\bnews\b",
    r"\bkhabar\b",
    r"खबर",
    r"\bfilm\b",
    r"\bmovie\b",
    r"\bserial\b",
    r"\bweb series\b",
    r"\b(?:tv|crime|reality) show\b",
    r"\bvillain\b",
    r"\bthriller\b",
    r"\bdetective\b",
    r"\bhero\b",
    r"\bnewsletter\b",
    r"\bdocumentary\b",
    r"\bepisode\b",
    r"\bin the film\b",
    r"\bpretend(?:ed|ing|s)\b",
    r"\blost .{0,25}\b(?:lakhs?|crores?)\b",
    r"फिल्म",
    r"सीरियल",
    r"वेब सीरीज़?",
    r"\bpicture me\b",
    # relayed / quoted speech ("someone said you are under ...", "caller bol raha tha")
    r"\b(?:someone|somebody|a man|a woman|a caller|the caller|they|he|she|mom|mum|mother|dad|papa|father|mummy|friend|neighbou?r|colleague)\b.{0,40}\b(?:said|says|told|tells|called|claimed|claiming|asked|saying)\b",
    r"\bif (?:someone|somebody|anyone|a caller)\b",
    r"\b(?:said|told me|tells you) that\b",
    r"\b(?:bol raha tha|bol rahi thi|bola tha|kaha tha|keh raha tha|keh rahi thi|bata raha tha|call aaya tha|ne kaha tha|ne bola tha)\b",
    r"\bcaller\b.{0,30}\b(?:bol|keh|kah)",
    r"बोल रहा था",
    r"बोल रही थी",
    r"कहा था",
    r"कॉल आया था",
    r"कॉलर",
    # visiting a branch / store is the legitimate channel
    r"\b(?:visit|come to|go to|reach)\b.{0,30}\b(?:branch|store|office)\b",
    r"शाखा",
    # official payment channels (challan / portal / counter) and private surprises
    r"\bchallan\b",
    r"\becha[l]*lan\b",
    r"\bofficial (?:portal|website|app)\b",
    r"\bportal\b",
    r"\bcourt counter\b",
    r"चालान",
    r"आधिकारिक (?:पोर्टल|वेबसाइट)",
    r"\bsurprise\b",
    r"\bbirthday\b",
    r"\bparty\b",
    r"\bcaterer\b",
    r"सरप्राइज़?",
    r"पार्टी",
)

_AUTH_NAME = _rx(
    r"\bcbi\b",
    r"central bureau of investigation",
    r"enforcement directorate",
    r"\bed (?:officer|office|team)\b",
    r"\bcustoms?\b",
    r"cyber ?(?:crime|cell|police|department)",
    r"crime branch",
    r"\bpolice\b",
    r"\binspector\b",
    r"\bdcp\b",
    r"deputy commissioner",
    r"\btrai\b",
    r"telecom (?:regulator|authority|department)",
    r"narcotics",
    r"\bncb\b",
    r"income tax",
    r"supreme court",
    r"anti-terror",
    r"\bncb\b",
    r"सीबीआई",
    r"प्रवर्तन निदेशालय",
    r"ईडी",
    r"कस्टम(?!र)",
    r"साइबर\s*(?:क्राइम|सेल|पुलिस)",
    r"क्राइम ब्रांच",
    r"पुलिस",
    r"इंस्पेक्टर",
    r"डीसीपी",
    r"ट्राई",
    r"टेलीकॉम",
    r"नारकोटिक्स",
    r"सुप्रीम कोर्ट",
    r"इनकम टैक्स",
    r"आतंकवाद",
    r"हमारे (?:वरिष्ठ )?अधिकारी",
)
_AUTH_FRAME = _rx(
    r"\bthis is\b",
    r"\bi am\b",
    r"\bi'm\b",
    r"\bmy name is\b",
    r"\bcalling (?:you )?from\b",
    r"\bcall(?:ing)? (?:is )?from\b",
    r"\bspeaking from\b",
    r"\bofficial call\b",
    r"\bbadge\b",
    r"\bi represent\b",
    r"\bofficer \w+ (?:from|of)\b",
    r"\bfrom (?:the )?(?:\w+ )?(?:cbi|crime branch|cyber|police|customs|narcotics|trai)\b",
    r"\bwill (?:disconnect|block|cancel|join|arrest)\b",
    r"\bwill be (?:blocked|disconnected|cancelled)\b",
    r"\bby trai\b",
    r"\b(?:customs|police|cbi|crime branch|ed|narcotics|trai) ne\b",
    r"\b(?:our|hamare) (?:senior )?officer\b",
    r"हमारे (?:वरिष्ठ )?अधिकारी",
    r"(?:कस्टम्स|कस्टम|पुलिस|सीबीआई|ईडी|ट्राई) (?:ने|से)",
    r"\bwe are (?:from|calling)\b",
    r"\b\w+ here from\b",
    r"\bmain\b.{0,40}\bbol\b",
    r"\bbol (?:raha|rahi|rahe)\b",
    r"\bmera naam\b",
    r"\bse bol\b",
    r"\bse (?:baat|call)\b",
    r"\bki taraf se\b",
    r"\bke taraf se\b",
    r"\bofficial\b",
    r"\byeh\b.{0,30}\bhai\b",
    r"\bhum\b.{0,40}\bse\b",
    r"\bhoon\b",
    r"मैं(?!ने)",
    r"मेरा नाम",
    r"बोल (?:रहा|रही|रहे)",
    r"से बोल",
    r"की तरफ",
    r"बैज",
    r"ऑफिशियल",
    r"से (?:बात|कॉल)",
    r"यह\b.{0,30}\bहै",
    r"हम\b.{0,40}\bसे",
    r"\bहूँ",
    r"हूं",
)

_LEGAL_STRONG = _rx(
    r"\bfir\b",
    r"\bpmla\b",
    r"\bndps\b",
    r"\bwarrant",
    r"money laundering",
    r"obstruction",
    r"\bcriminal (?:offen[cs]e|case)\b",
    r"\bcase (?:is |has been )?(?:registered|filed)\b",
    r"\bcase (?:no|number)\b",
    r"\bdarj\b",
    r"\bgambhir case\b",
    r"\bblack money\b",
    r"\bsuspect\b",
    r"\b(?:parcel|courier|package|consignment)\b.{0,80}\b(?:seiz|intercept|caught|found|pakda|mila|mili|contraband|drugs|mdma|passports?|banned|illegal|nashil|nakli|fake)",
    r"\b(?:drugs|mdma|contraband|narcotics|banned substances|nashil\w*|prohibited)\b",
    r"\b(?:aadhaar|aadhar|sim|number|phone)\b.{0,60}\b(?:linked|juda|connected|used|misuse|found)\b.{0,60}\b(?:illegal|crime|trafficking|laundering|harass|threat|fraud|terror|case|drug|seized)",
    r"\b(?:harassment|threatening|dhamki|illegal) (?:calls|messages|activity|website)\b",
    r"\billegal parcel\b",
    r"\bunlawful\b",
    r"\bnon-bailable\b",
    r"\bbailable\b",
    r"\btrafficking\b",
    r"एफआईआर",
    r"वारंट",
    r"मनी लॉन्ड्रिंग",
    r"काला धन",
    r"मामला दर्ज",
    r"केस दर्ज",
    r"न्याय में बाधा",
    r"पार्सल.{0,80}(?:पकड़|मिला|जब्त|ड्रग|प्रतिबंधित|नकली|अवैध)",
    r"ड्रग",
    r"प्रतिबंधित",
    r"मानव तस्करी",
    r"(?:आधार|सिम|नंबर).{0,60}(?:जुड़ा|जुड़े|इस्तेमाल).{0,60}(?:अवैध|केस|तस्करी|धमकी|मामले)",
    r"उत्पीड़न",
    r"धमकी",
    r"अवैध",
    r"गैर-जमानती",
    r"\bsuspect\b",
    r"संदिग्ध",
    r"\bnashili\b",
    r"\bgunaah\b",
    r"\bgunah\b",
    r"जुर्म|अपराध",
)
_LEGAL_WEAK = _rx(
    r"\barrest",
    r"\bgiraft\w*",
    r"\bjail\b",
    r"\bjaana padega\b",
    r"गिरफ्त",
    r"\bजेल\b",
    r"\b(?:number|sim)\b.{0,50}\b(?:blocked?|disconnect|cancel|band)\b",
    r"(?:नंबर|सिम).{0,50}(?:बंद|ब्लॉक)",
)
_DIGITAL_ARREST = _rx(
    r"\byou\b.{0,40}\b(?:under|in)\s+digital(?:ly)?[\s_-]*(?:arrest|custody|detention)",
    r"\b(?:placing|placed|place|put|putting|keep|keeping|kept) you\b.{0,25}digital[\s_-]*(?:arrest|custody|detention)",
    r"\bwe (?:are|have|will)\b.{0,30}digital[\s_-]*arrest",
    r"\baap\b.{0,40}digital[\s_-]*arrest",
    r"\bdigital[\s_-]*arrest\s*(?:kiya|me daal|mein daal|me rakh|me hain|mein hain)",
    r"आप.{0,40}डिजिटल[\s_-]*अरेस्ट",
    r"डिजिटल[\s_-]*अरेस्ट\s*(?:किया|में डाल|में रख|में हैं)",
)
_DA_ANY = _rx(r"digital(?:ly)?[\s_-]*arrest", r"डिजिटल[\s_-]*अरेस्ट")
_ISO_STAY = _rx(
    r"\b(?:stay|remain|keep|stick) (?:on|with)? ?(?:the |this |your )?(?:video |phone )?(?:call|line)\b",
    r"\bkeep (?:the |this )?call connected\b",
    r"\bstay connected\b",
    r"\b(?:do not|don't|dont|never|cannot|can't|not allowed to) (?:disconnect|hang up|cut|end|close|leave|drop|switch off)\b",
    r"\bnot allowed to disconnect\b",
    r"\bcall (?:mat |nahi )(?:disconnect|kaat|band)",
    r"\bcall (?:disconnect|kaat) (?:mat|nahi)\b",
    r"\bdisconnect (?:mat|nahi)\b",
    r"\bvideo call\b.{0,12}\b(?:mat|nahi) (?:kaat|kat|band|chhod|disconnect)",
    r"\b(?:mat|nahi)\b.{0,25}\b(?:disconnect|kaat|chhod)",
    r"\bline par (?:bane )?r[ae][hk]",
    r"\bbane r[ae][hk]",
    r"\bconnected rakh",
    r"\bcall par bane\b",
    r"लाइन पर (?:बने )?रह",
    r"बने रह",
    r"कनेक्टेड रख",
    r"कॉल\s*मत\s*(?:काट|डिस्कनेक्ट|बंद)",
    r"कॉल\s*(?:काट|डिस्कनेक्ट|बंद)\w*\s*(?:मत|नहीं)",
    r"डिस्कनेक्ट\s*(?:मत|नहीं)",
    r"वीडियो कॉल\s*(?:बंद|मत|छोड़)",
    r"(?:मत|नहीं)\s*(?:काट|छोड़|डिस्कनेक्ट)",
)
_ISO_SECRET = _rx(
    r"\b(?:do not|don't|dont|not|never|mustn't|must not) (?:\w+ )?(?:tell|inform|mention|speak|talk|share this|disclose)\b.{0,40}\b(?:anyone|anybody|nobody|family|relatives|neighbours|neighbors|lawyer|bank|others|else|wife|husband|colleagues?|friends|parents|mother|father|boss|office|son|daughter|brother|sister)\b",
    r"\btell (?:no one|nobody|no-one)\b",
    r"\bkeep (?:this|it|the matter) (?:a )?(?:secret|confidential|between us)\b",
    r"\bkeep this secret\b",
    r"\bstrictly (?:confidential|between us)\b",
    r"\bsealed case\b",
    r"\bsecret investigation\b",
    r"\b(?:do not|don't) (?:call|contact) anyone\b",
    r"\bkisi (?:ko|se) (?:bhi )?(?:mat|nahi|na)\b",
    r"\bkisi (?:ko|se)\b.{0,20}\b(?:mat|nahi)\b",
    r"\b(?:mat|nahi) (?:bataiye|batayiye|batana|bataana|batao|batayen|bolna|kehna|kijiye)\b.{0,0}",
    r"\b(?:ghar|parivar|rishte|padosi|vakeel)\w*\b.{0,30}\b(?:mat|nahi)\b",
    r"\bgopni(?:ya|y)\b",
    r"\bsecret rakh",
    r"\bconfidential hai\b",
    r"\bkisi (?:aur )?(?:ko|se)\b.{0,20}\b(?:call|baat) (?:mat|nahi)\b",
    r"\bsealed\b",
    r"किसी (?:को|से|और)\s*(?:भी )?(?:मत|न|नहीं)",
    r"(?:मत|नहीं)\s*(?:बताना|बताइए|बताओ|बताएं|बोलना)",
    r"गोपनीय",
    r"सीक्रेट",
    r"सीलबंद",
    r"(?:घर|परिवार|रिश्ते|पड़ोसी|वकील).{0,30}(?:मत|नहीं)",
    r"किसी और को कॉल",
)
_ISO_CONFINE = _rx(
    r"\bkeep (?:your )?(?:phone )?camera (?:on|switched on|pointed|facing|focused)\b",
    r"\bcamera (?:pointed|facing) (?:at|on) you\b",
    r"\bkeep your (?:phone )?camera\b",
    r"\bcamera (?:on|chalu) rakh",
    r"\bswitch on your camera\b",
    r"\b(?:not|never|don't|do not) leave (?:the |your )?room\b",
    r"\bstay in (?:the |your )?room\b",
    r"\bin front of the camera\b",
    r"\bkamre se bahar (?:mat|nahi)\b",
    r"\bkamre me rah",
    r"\bcamera ke saamne\b",
    r"\bnot step out\b",
    r"\bdo not step out\b",
    r"कैमरा\s*(?:ऑन|चालू)\s*रख",
    r"कमरे\s*(?:से बाहर|में)\s*(?:मत|रह|नहीं)",
    r"कैमरे के सामने",
    r"कमरे में रहिए",
)
_MOVE_VERB = r"(?:transfer|send|move|deposit|put|park|pay|daliye|daaliye|jama|bhej\w*|dal\w*|ट्रांसफर|जमा|डाल\w*|भेज\w*)"
_SAFE_WORD = r"(?:safe|secure|secured|protected|verification|government|govt|rbi|custody|सेफ|सुरक्षित|वेरिफिकेशन|सरकारी|आरबीआई|rbi ke)"
_ACCT_WORD = r"(?:account|accounts|khata|khate|wallet|अकाउंट|खाते|खाता)"
_SAFE_ACCOUNT = _rx(
    rf"\b{_MOVE_VERB}\b.{{0,80}}\b{_SAFE_WORD}\b.{{0,25}}{_ACCT_WORD}",
    rf"\b{_SAFE_WORD}\b.{{0,25}}{_ACCT_WORD}.{{0,40}}\b{_MOVE_VERB}\b",
    rf"{_MOVE_VERB}.{{0,80}}{_SAFE_WORD}.{{0,25}}{_ACCT_WORD}",
    rf"{_SAFE_WORD}.{{0,25}}{_ACCT_WORD}.{{0,40}}{_MOVE_VERB}",
)
_MOVE_FUNDS = _rx(
    r"\btransfer\b.{0,30}\b(?:funds?|money|amount|balance|savings|paise|paisa|rakam|bachat)\b",
    r"\b(?:funds?|money|amount|balance|savings|paise|paisa|rakam|bachat)\b.{0,40}\btransfer\b",
    r"(?:पैसे|रकम|राशि|बैलेंस|बचत).{0,40}ट्रांसफर",
    r"ट्रांसफर.{0,30}(?:पैसे|रकम|राशि|बचत)",
)
_VERIF_CONTEXT = _rx(
    r"\bverif(?:y|ication)\b",
    r"\baudit\b",
    r"\brefund",
    r"\bwill be returned\b",
    r"\bwapas\b",
    r"\bcustody\b",
    r"\binvestigation\b",
    r"\bjaanch\b",
    r"\bsafe\b",
    r"आरबीआई",
    r"वेरिफ",
    r"ऑडिट",
    r"रिफंड",
    r"वापस",
    r"जांच",
    r"सेफ",
    r"हिरासत",
)
_PAY_VERB = _rx(
    r"\b(?:pay|send|transfer|deposit|bhej\w*|jama|de dijiye|dijiye)\b",
    r"भेज\w*",
    r"जमा",
    r"ट्रांसफर",
    r"भुगतान",
)
_PAY_OBJ = _rx(
    r"\b(?:deposit|rupees|rs\.?|amount|upi id|upi|settlement|clearance|paise|paisa|funds?|money|fine)\b",
    r"डिपॉजिट",
    r"रुपये",
    r"पैसे",
    r"यूपीआई",
    r"सेटलमेंट",
    r"राशि",
    r"रकम",
)
_PAY_CONTEXT = _rx(
    r"\barrest",
    r"\bjail\b",
    r"\bsettle",
    r"\bbail\b",
    r"\bgiraft\w*",
    r"\bwarrant",
    r"\brelease him\b",
    r"\bclose the case\b",
    r"\bcase\b",
    r"\bchhud\w*",
    r"\bdetained\b",
    r"\bclearance\b",
    r"गिरफ्त",
    r"जेल",
    r"छुड़ा",
    r"केस",
    r"वारंट",
    r"जमानत",
    r"अरेस्ट",
    r"सेटलमेंट",
)
_VERIFY_ASK = _rx(
    r"\bverify\b.{0,40}\b(?:account|savings|balance|aadhaar|pan|everything)\b",
    r"\b(?:account|savings|balance)\b.{0,30}\bverif(?:y|ication)\b",
    r"\bverif(?:y|ication)\b.{0,40}\b(?:account|savings|balance)\b",
    r"\btell me your\b.{0,40}\b(?:balance|account|upi|bank|aadhaar|pan)\b",
    r"\b(?:share|confirm|give|tell|batayiye|batao|bataiye)\b.{0,25}\b(?:aadhaar|aadhar|pan)\b",
    r"\b(?:aadhaar|aadhar)\b.{0,20}\b(?:aur|and|&)\b.{0,12}\bpan\b.{0,25}\b(?:abhi|now|batayiye|bataiye|batao|share|confirm|details)\b",
    r"\b(?:show|dikhaiye|dikhao|display)\b.{0,30}\b(?:balance|banking app|net banking|bank balance)\b",
    r"\bopen your (?:banking app|net banking)\b",
    r"\bnet banking kholkar\b",
    r"\bbalance (?:batayiye|bataiye|batao|dikhaiye|dikhao)\b",
    r"\bbanking app kholkar\b",
    r"\baccount (?:aur savings )?verify\b",
    r"\bbank balance\b.{0,25}\b(?:bataiye|batayiye|batao|share)\b",
    r"\bbank ka naam\b.{0,30}\bbalance\b",
    r"आधार\s*(?:और|&)?\s*पैन.{0,25}(?:बताइए|बताओ|शेयर|कन्फर्म|भेजिए|दीजिए)",
    r"(?:बताइए|बताओ|शेयर|कन्फर्म|भेजिए|दीजिए).{0,25}आधार",
    r"(?:अकाउंट|खाता).{0,30}वेरिफ",
    r"बैलेंस\s*(?:बताइए|दिखाइए|बताओ)",
    r"बैंक बैलेंस.{0,25}बताइए",
    r"बैंकिंग ऐप खोल",
    r"नेट बैंकिंग खोल",
    r"वेरिफाई करना है",
    r"verify karna hai",
)
_CRED_ASK = _rx(
    r"\b(?:share|tell|send|give|read out|batao|bataiye|bhejiye|bata do)\b.{0,25}\b(?:otp|pin|cvv|password)\b",
    r"(?:बताइए|बताओ|भेजिए|शेयर).{0,20}(?:OTP|PIN|CVV|पासवर्ड|ओटीपी|पिन)",
    r"(?:OTP|ओटीपी|PIN|CVV).{0,15}(?:बताइए|बताओ|भेजिए|शेयर)",
    r"\b(?:otp|pin|cvv)\b.{0,12}\b(?:bataiye|batayiye|batao|bhejiye)\b",
)
_URGENCY = _rx(
    r"\bonly (?:\w+ )?(?:minutes?|hours?)\b",
    r"\bwithin (?:\w+ )?(?:minutes?|two hours|2 hours|one hour)\b",
    r"\bimmediately\b",
    r"\bnow\b",
    r"\bright now\b",
    r"\bat once\b",
    r"\burgent(?:ly)?\b",
    r"\bturant\b",
    r"\btatkal\b",
    r"\babhi\b",
    r"\bsirf \w+ minute\b",
    r"\b(?:aaj hi|today itself)\b",
    r"\bdo ghante\b",
    r"तुरंत",
    r"तत्काल",
    r"ज़रूरी",
    r"जरूरी",
    r"सिर्फ \S+ मिनट",
    r"अभी",
    r"आज ही",
    r"दो घंटे",
)

_SPECS = {
    "DIGITAL_ARREST_PHRASE": "caller says the victim is under 'digital arrest'",
    "SAFE_ACCOUNT_TRANSFER": "caller demands moving funds to a 'safe'/RBI/verification account",
    "ISOLATION_DEMAND": "caller demands the victim stay on the call, stay silent or stay in view",
    "PAYMENT_DEMAND": "caller demands money in connection with an arrest or case",
    "AUTHORITY_IMPERSONATION": "caller claims to be a law-enforcement or regulatory body",
    "LEGAL_THREAT": "caller alleges a criminal case, warrant, seized parcel or similar",
    "ACCOUNT_VERIFICATION_ASK": "caller asks to 'verify' the victim's account, balance or ID",
    "CREDENTIAL_ASK": "caller asks for an OTP, PIN or password",
    "URGENCY": "caller applies time pressure",
}
_ORDER = tuple(_SPECS)
_WEIGHT = {
    "DIGITAL_ARREST_PHRASE": 0.69,
    "SAFE_ACCOUNT_TRANSFER": 0.69,
    "ISOLATION_DEMAND": 0.55,
    "PAYMENT_DEMAND": 0.50,
    "AUTHORITY_IMPERSONATION": 0.40,
    "LEGAL_THREAT": 0.55,
    "ACCOUNT_VERIFICATION_ASK": 0.45,
    "CREDENTIAL_ASK": 0.35,
    "URGENCY": 0.15,
}
W_ISOLATION_MULTI = 0.75
W_LEGAL_WEAK = 0.30


def _effective_clauses(sentence: str) -> str:
    kept = [c for c in _CLAUSE_SPLIT.split(sentence) if c.strip() and not _ADVISORY.search(c)]
    return " , ".join(kept)


def _cues(s: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if _DIGITAL_ARREST.search(s):
        out["DIGITAL_ARREST_PHRASE"] = _WEIGHT["DIGITAL_ARREST_PHRASE"]
    if _SAFE_ACCOUNT.search(s) or (_MOVE_FUNDS.search(s) and _VERIF_CONTEXT.search(s)):
        out["SAFE_ACCOUNT_TRANSFER"] = _WEIGHT["SAFE_ACCOUNT_TRANSFER"]
    subtypes = sum(bool(p.search(s)) for p in (_ISO_STAY, _ISO_SECRET, _ISO_CONFINE))
    if subtypes:
        out["ISOLATION_DEMAND"] = (
            W_ISOLATION_MULTI if subtypes >= 2 else _WEIGHT["ISOLATION_DEMAND"]
        )
    if _PAY_VERB.search(s) and _PAY_OBJ.search(s) and _PAY_CONTEXT.search(s):
        out["PAYMENT_DEMAND"] = _WEIGHT["PAYMENT_DEMAND"]
    if _AUTH_NAME.search(s) and _AUTH_FRAME.search(s):
        out["AUTHORITY_IMPERSONATION"] = _WEIGHT["AUTHORITY_IMPERSONATION"]
    s_legal = _DA_ANY.sub(" ", s)  # "arrest" inside "digital arrest" is its own cue
    if _LEGAL_STRONG.search(s_legal):
        out["LEGAL_THREAT"] = _WEIGHT["LEGAL_THREAT"]
    elif _LEGAL_WEAK.search(s_legal):
        out["LEGAL_THREAT"] = W_LEGAL_WEAK
    if _VERIFY_ASK.search(s):
        out["ACCOUNT_VERIFICATION_ASK"] = _WEIGHT["ACCOUNT_VERIFICATION_ASK"]
    if _CRED_ASK.search(s):
        out["CREDENTIAL_ASK"] = _WEIGHT["CREDENTIAL_ASK"]
    if _URGENCY.search(s):
        out["URGENCY"] = _WEIGHT["URGENCY"]
    return out


def noisy_or(weights: list[float]) -> float:
    return 1.0 - reduce(lambda acc, w: acc * (1.0 - w), weights, 1.0)


def analyse(text: str) -> tuple[float, list[Reason], bool]:
    """Rules score, reasons, and whether any clause was dropped as advisory."""
    found: dict[str, float] = {}
    advisory = False
    for sent in _SENT_SPLIT.split(_norm(text)):
        if not sent.strip():
            continue
        if _BENIGN_SENT.search(sent):
            advisory = True
            continue
        eff = _effective_clauses(sent)
        advisory = advisory or _ADVISORY.search(sent) is not None
        if not eff:
            continue
        for code, w in _cues(eff).items():
            found[code] = max(found.get(code, 0.0), w)
    reasons = [Reason(code=c, weight=found[c], detail=_SPECS[c]) for c in _ORDER if c in found]
    return round(min(1.0, noisy_or([r.weight for r in reasons])), 4), reasons, advisory


def score_text(text: str) -> tuple[float, list[Reason]]:
    """Rules-only score and reasons (no reasons for text with no cues)."""
    score, reasons, _ = analyse(text)
    return score, reasons


def score_chunk(event: CallEvent) -> tuple[float, list[Reason]]:
    """Rules-only score for one transcript chunk (``rules-v1``)."""
    return score_text(event.transcript_chunk)
