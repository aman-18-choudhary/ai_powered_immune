"""Hand-written hard negatives: benign texts that mention scam vocabulary in a safe context.

Used both as extra classifier training data and as regression tests for the rules.
(en, romanised Hindi, Devanagari Hindi.)
"""

HARD_NEGATIVES: list[str] = [
    "We will never ask for your OTP or PIN. If anyone does, hang up and report it.",
    "Please ignore calls about safe accounts, no bank or RBI official will ask you to move money.",
    "The RBI never asks customers to transfer funds to a safe account, so ignore such calls and report them.",
    "Beware of digital arrest scams. No agency can put you under digital arrest on a video call.",
    "This is a fraud awareness message from your bank. Never share your OTP with anyone.",
    "Police and the CBI will never demand money over the phone, so please do not fall for such calls.",
    "I filed a complaint at the police station yesterday about my lost phone, they gave me an FIR copy.",
    "The RBI has kept the repo rate unchanged this quarter, so your home loan EMI will stay the same.",
    "My cousin joined the police force last year and he is posted in Pune.",
    "Your RBI ombudsman complaint has been closed in your favour and the amount is credited to your account.",
    "Customs duty on your imported parcel is already included, nothing more is due.",
    "Your cheque has been verified and cleared, the balance will reflect by tomorrow morning.",
    "Hum kabhi aapka OTP ya PIN nahi maangte. Koi maange to call kaat dijiye aur report kijiye.",
    "Safe account me paise transfer karne wali calls ko ignore kijiye, RBI ya bank aisa kabhi nahi kehte.",
    "Digital arrest ek scam hai, koi bhi agency video call par aapko arrest nahi karti. Savdhan rahiye.",
    "Maine kal police station me phone kho jaane ki complaint likhwayi hai.",
    "RBI ne repo rate nahi badla hai, isliye aapki EMI wahi rahegi.",
    "हम कभी आपका OTP या PIN नहीं मांगते। कोई मांगे तो कॉल काटकर रिपोर्ट कीजिए।",
    "सेफ अकाउंट में पैसे ट्रांसफर करने वाली कॉल को अनदेखा कीजिए, RBI या बैंक ऐसा कभी नहीं कहते।",
    "डिजिटल अरेस्ट एक स्कैम है, कोई भी एजेंसी वीडियो कॉल पर गिरफ्तार नहीं करती। सावधान रहिए।",
    "मैंने कल पुलिस स्टेशन में फोन खोने की शिकायत दर्ज करवाई है।",
    "RBI ने रेपो रेट नहीं बदला है, इसलिए आपकी EMI वही रहेगी।",
]
