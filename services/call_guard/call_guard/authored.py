"""Hand-authored (non-simulator) training text for the classifier.

Scam text is composed from varied building blocks (authorities, allegations, isolation and
payment demands, verification asks, threats) in English, romanised Hindi and Devanagari;
benign text is a broad set of ordinary calls plus hard negatives (utility/loan reminders,
courier OTP, police friends, TV shows, court dates, airport customs, genuine bank awareness).
Nothing here is shared with ``tests/data/independent_eval.py``.
"""

import numpy as np

# ------------------------------------------------------------------ scam building blocks
INTRO = {
    "en": [
        "Good morning, this is Inspector {n} from the {a}.",
        "I am calling from the {a}, my badge number is {c}.",
        "You are speaking with Officer {n}, {a}.",
        "This call is from the {a}. Please listen carefully.",
        "I represent the {a} and I need to speak to you about a pending file.",
        "Namaste, {a} se Inspector {n} here, kindly confirm your name.",
    ],
    "hi-Latn": [
        "Namaste, main {a} se Inspector {n} bol raha hoon.",
        "Yeh {a} ki taraf se call hai, mera badge number {c} hai.",
        "Aap {a} ke Officer {n} se baat kar rahe hain.",
        "Main {a} se bol raha hoon, dhyan se suniye.",
        "Hum {a} se bol rahe hain, aapki ek file pending hai.",
    ],
    "hi": [
        "नमस्ते, मैं {a} से इंस्पेक्टर {n} बोल रहा हूँ।",
        "यह {a} की तरफ से कॉल है, मेरा बैज नंबर {c} है।",
        "आप {a} के ऑफिसर {n} से बात कर रहे हैं।",
        "मैं {a} से बोल रहा हूँ, ध्यान से सुनिए।",
        "हम {a} से बोल रहे हैं, आपकी एक फाइल पेंडिंग है।",
    ],
}
AUTH = {
    "en": [
        "CBI",
        "Enforcement Directorate",
        "cyber crime branch",
        "Mumbai Customs",
        "Narcotics Control Bureau",
        "TRAI",
        "Delhi Police cyber cell",
        "Supreme Court registry",
        "income tax and ED team",
        "crime branch",
        "anti-terror squad",
        "telecom department",
    ],
    "hi-Latn": [
        "CBI",
        "Enforcement Directorate",
        "cyber crime branch",
        "Mumbai Customs",
        "Narcotics Bureau",
        "TRAI",
        "Delhi Police cyber cell",
        "Supreme Court registry",
        "crime branch",
        "ED office",
    ],
    "hi": [
        "सीबीआई",
        "प्रवर्तन निदेशालय",
        "साइबर क्राइम ब्रांच",
        "मुंबई कस्टम्स",
        "नारकोटिक्स ब्यूरो",
        "ट्राई",
        "दिल्ली पुलिस साइबर सेल",
        "सुप्रीम कोर्ट रजिस्ट्री",
        "क्राइम ब्रांच",
        "ईडी ऑफिस",
    ],
}
ALLEGE = {
    "en": [
        "Your Aadhaar number is linked to an illegal parcel with drugs and fake passports.",
        "A courier in your name was intercepted at the airport carrying contraband.",
        "Your SIM card has been used for harassment calls and illegal advertisements.",
        "Your mobile number will be disconnected in two hours because of complaints against it.",
        "Your bank account is named in a money laundering case worth crores.",
        "A non-bailable warrant has been issued in your name.",
        "An FIR has been registered against you under PMLA.",
        "You are a suspect in a financial fraud and trafficking investigation.",
        "Your son has been detained in a narcotics case and is in our custody.",
        "We found black money routed through your account.",
    ],
    "hi-Latn": [
        "Aapka Aadhaar ek illegal parcel se juda hai jisme drugs aur nakli passport mile hain.",
        "Aapke naam ka ek courier airport par pakda gaya hai, usme contraband tha.",
        "Aapke SIM se harassment calls aur illegal ads ho rahe hain.",
        "Aapka mobile number do ghante me band ho jayega kyunki uske khilaf complaints hain.",
        "Aapka bank account ek crore wale money laundering case me aaya hai.",
        "Aapke naam non-bailable warrant jaari hua hai.",
        "Aapke khilaf PMLA ke tahat FIR darj hui hai.",
        "Aap ek financial fraud aur trafficking jaanch me suspect hain.",
        "Aapka beta narcotics case me pakda gaya hai aur hamari custody me hai.",
        "Aapke account se kala dhan ghumaya gaya hai.",
    ],
    "hi": [
        "आपका आधार एक अवैध पार्सल से जुड़ा है जिसमें ड्रग्स और नकली पासपोर्ट मिले हैं।",
        "आपके नाम का एक कूरियर एयरपोर्ट पर पकड़ा गया है, उसमें प्रतिबंधित सामान था।",
        "आपके सिम से उत्पीड़न कॉल और अवैध विज्ञापन हो रहे हैं।",
        "आपका मोबाइल नंबर दो घंटे में बंद हो जाएगा क्योंकि उसके खिलाफ शिकायतें हैं।",
        "आपका बैंक खाता करोड़ों के मनी लॉन्ड्रिंग केस में आया है।",
        "आपके नाम गैर-जमानती वारंट जारी हुआ है।",
        "आपके खिलाफ पीएमएलए के तहत एफआईआर दर्ज हुई है।",
        "आप एक वित्तीय धोखाधड़ी और तस्करी की जांच में संदिग्ध हैं।",
        "आपका बेटा नारकोटिक्स केस में पकड़ा गया है और हमारी कस्टडी में है।",
        "आपके खाते से काला धन घुमाया गया है।",
    ],
}
ISOLATE = {
    "en": [
        "You are now under digital arrest, do not disconnect this video call.",
        "Stay on the call and keep your camera on until I say otherwise.",
        "Do not tell anyone, not your family, not your bank, this is a confidential matter.",
        "Do not leave the room and do not speak to your neighbours about this.",
        "If you inform anybody it will be treated as obstruction of justice.",
        "You cannot hang up; remain on the line until the officer clears you.",
        "Keep this strictly between us, it is a sealed case.",
    ],
    "hi-Latn": [
        "Aap abhi se digital arrest me hain, yeh video call disconnect mat kijiye.",
        "Call par bane rahiye aur camera on rakhiye jab tak main na bolun.",
        "Kisi ko mat batana, na ghar walon ko na bank ko, yeh gopniya mamla hai.",
        "Kamre se bahar mat jaiye aur padosiyon se is baare me baat mat kijiye.",
        "Kisi ko bataya to yeh obstruction of justice maana jayega.",
        "Aap call kaat nahi sakte, officer clear karein tab tak line par rahiye.",
        "Yeh sirf humare beech rahe, yeh sealed case hai.",
    ],
    "hi": [
        "आप अभी से डिजिटल अरेस्ट में हैं, यह वीडियो कॉल डिस्कनेक्ट मत कीजिए।",
        "कॉल पर बने रहिए और कैमरा ऑन रखिए जब तक मैं न कहूँ।",
        "किसी को मत बताना, न घरवालों को न बैंक को, यह गोपनीय मामला है।",
        "कमरे से बाहर मत जाइए और पड़ोसियों से इस बारे में बात मत कीजिए।",
        "किसी को बताया तो इसे न्याय में बाधा माना जाएगा।",
        "आप कॉल काट नहीं सकते, अधिकारी क्लियर करें तब तक लाइन पर रहिए।",
        "यह सिर्फ हमारे बीच रहे, यह सीलबंद केस है।",
    ],
}
DEMAND = {
    "en": [
        "Transfer all your savings to the RBI safe account for verification right now.",
        "Deposit your funds in the government protected account, they will be returned after the audit.",
        "Pay the security deposit immediately to avoid arrest.",
        "Send the settlement amount to this UPI ID to close the case.",
        "Move your money into the verification account, I will message the details.",
        "You need to put the balance in the secure RBI account today for temporary custody.",
        "Pay the clearance fee now, otherwise the warrant will be executed.",
    ],
    "hi-Latn": [
        "Apni saari bachat abhi RBI ke safe account me transfer kijiye verification ke liye.",
        "Paise government protected account me jama kijiye, audit ke baad wapas honge.",
        "Giraftari se bachna hai to security deposit turant bhejiye.",
        "Settlement ke paise is UPI ID par bhejiye taaki case band ho.",
        "Apna paisa verification account me daaliye, main details message karta hoon.",
        "Aaj hi balance secure RBI account me daliye, temporary custody ke liye.",
        "Clearance fee abhi bhejiye warna warrant execute ho jayega.",
    ],
    "hi": [
        "अपनी सारी बचत अभी आरबीआई के सेफ अकाउंट में ट्रांसफर कीजिए वेरिफिकेशन के लिए।",
        "पैसे सरकारी सुरक्षित खाते में जमा कीजिए, ऑडिट के बाद वापस होंगे।",
        "गिरफ्तारी से बचना है तो सिक्योरिटी डिपॉजिट तुरंत भेजिए।",
        "सेटलमेंट के पैसे इस यूपीआई आईडी पर भेजिए ताकि केस बंद हो।",
        "अपना पैसा वेरिफिकेशन खाते में डालिए, मैं डिटेल्स मैसेज करता हूँ।",
        "आज ही बैलेंस सुरक्षित आरबीआई खाते में डालिए, अस्थायी कस्टडी के लिए।",
        "क्लियरेंस फीस अभी भेजिए वरना वारंट लागू हो जाएगा।",
    ],
}
VERIFY = {
    "en": [
        "Open your banking app and show me the balance so we can verify your account.",
        "Tell me your Aadhaar and PAN details now.",
        "Share your account balance and savings, we must verify everything.",
        "Confirm your PAN and Aadhaar, and which UPI app you use.",
    ],
    "hi-Latn": [
        "Banking app kholkar balance dikhaiye taaki hum aapka account verify kar sakein.",
        "Apna Aadhaar aur PAN ki details abhi batayiye.",
        "Account balance aur savings share kijiye, hume sab verify karna hai.",
        "PAN aur Aadhaar confirm kijiye aur batayiye kaunsa UPI app use karte hain.",
    ],
    "hi": [
        "बैंकिंग ऐप खोलकर बैलेंस दिखाइए ताकि हम आपका अकाउंट वेरिफाई कर सकें।",
        "अपने आधार और पैन की डिटेल्स अभी बताइए।",
        "अकाउंट बैलेंस और बचत शेयर कीजिए, हमें सब वेरिफाई करना है।",
        "पैन और आधार कन्फर्म कीजिए और बताइए कौन सा यूपीआई ऐप इस्तेमाल करते हैं।",
    ],
}
THREAT = {
    "en": [
        "If you do not cooperate right now the team will reach your house and arrest you.",
        "You have only thirty minutes, after that the warrant is executed.",
        "Your family will also face arrest if you delay.",
    ],
    "hi-Latn": [
        "Agar aap abhi cooperate nahi karte to team ghar pahunch kar giraftar kar legi.",
        "Aapke paas sirf tees minute hain, uske baad warrant execute hoga.",
        "Der ki to parivar ko bhi giraftari ka saamna karna padega.",
    ],
    "hi": [
        "अगर आप अभी सहयोग नहीं करते तो टीम घर पहुंचकर गिरफ्तार कर लेगी।",
        "आपके पास सिर्फ तीस मिनट हैं, उसके बाद वारंट लागू होगा।",
        "देर की तो परिवार को भी गिरफ्तारी का सामना करना पड़ेगा।",
    ],
}
NAMES = ["Sharma", "Rathore", "Kulkarni", "Singh", "Verma", "Deshmukh", "Khan", "Rane"]
LANGS = ("en", "hi-Latn", "hi")
BLOCKS = (INTRO, ALLEGE, ISOLATE, DEMAND, VERIFY, THREAT)
BLOCK_P = (0.30, 0.45, 0.45, 0.40, 0.25, 0.25)


def scam_chunks(rng: np.random.Generator, n: int) -> list[str]:
    out: list[str] = []
    for _ in range(n):
        lang = LANGS[int(rng.integers(3))]
        picks = [b for b, p in zip(BLOCKS, BLOCK_P, strict=True) if rng.random() < p]
        if not picks:
            picks = [BLOCKS[int(rng.integers(len(BLOCKS)))]]
        picks = picks[:3]
        parts = []
        for b in picks:
            parts.append(b[lang][int(rng.integers(len(b[lang])))])
        text = " ".join(parts)
        text = (
            text.format(
                a=AUTH[lang][int(rng.integers(len(AUTH[lang])))],
                n=NAMES[int(rng.integers(len(NAMES)))],
                c=int(rng.integers(1000, 9999)),
            )
            if "{" in text
            else text
        )
        out.append(text)
    return out


# ------------------------------------------------------------------ benign (hand written)
BENIGN: list[str] = [
    # --- English: reminders, loans, telemarketing, delivery, family, work, health
    "Reminder from your electricity provider: the bill for August is overdue, please pay immediately through the app to avoid a late fee.",
    "Your gas cylinder booking is confirmed and will be delivered tomorrow before noon.",
    "This is a reminder that your home loan EMI of 18,400 rupees will be debited on the fifth. Please keep sufficient balance.",
    "Hello, I am calling from a finance company about a personal loan offer for salaried customers. Are you interested?",
    "Hi, this is the telecom operator. Your postpaid bill is due on Friday, you can pay using the app or at a store.",
    "Your credit card payment is overdue by two days, kindly pay urgently to avoid additional charges.",
    "Hello, courier partner here. An OTP has been sent to your phone, please tell it to me so I can give you the parcel.",
    "Your Amazon package arrives today. Our rider will call when he is outside your building.",
    "Hey, did you watch the match last night? That final over was unbelievable.",
    "I'll be late today, the meeting with the client got extended. Please start dinner without me.",
    "My friend is a police officer in Jaipur and he said the traffic rules are being tightened from next month.",
    "We saw a new crime thriller on TV where the detective arrests the banker for fraud. The ending was great.",
    "There was a news report about a cyber fraud gang being arrested by the police in Hyderabad.",
    "The police station near our house is getting a new building, they were talking about it in the society meeting.",
    "My lawyer says the hearing is on the 12th and the judge will probably only fix the next date.",
    "At the airport customs they only checked our bags and let us go in five minutes.",
    "The RBI has announced new rules for digital lending apps, my accountant explained it to me.",
    "RBI has launched a campaign telling people never to share their UPI PIN with anyone.",
    "Dear customer, be alert: fraudsters call pretending to be bank officials. Never share OTP, CVV or PIN.",
    "Banks and government agencies never ask you to transfer money to a safe account. If someone does, it is a scam, report it on the cyber crime portal.",
    "Public awareness: no officer can arrest you over a video call. If anyone says so, it is fraud, disconnect and call 1930.",
    "A message from your bank: we will never call you to verify your PIN or ask you to install a screen sharing app.",
    "Please verify your email address and update your mobile number on the app to keep receiving statements.",
    "Your KYC documents have been verified successfully and your account is fully active.",
    "Hello, I am calling from the bank to confirm whether you made a purchase of 3,200 rupees at a petrol pump today.",
    "Sir, your passport police verification is scheduled for Monday, an officer will visit your home between ten and twelve.",
    "Your income tax return has been processed and the refund will be credited in a week.",
    "I need to renew my driving licence, do you know if the RTO accepts online payment now?",
    "Is the doctor available tomorrow evening? My father needs to get his blood pressure checked.",
    "Sorry, I cannot talk right now, I am in a meeting. Can I call you back in an hour?",
    "Congratulations, you have been shortlisted for an interview at our company next Wednesday.",
    "Your insurance policy renewal is due. Pay before the 30th to avoid the lapse of cover.",
    "Please keep your Aadhaar and PAN card ready when you visit the branch for opening the account.",
    "The warranty on your washing machine expires next month, would you like to buy an extension?",
    "Do not tell anyone, but I am planning a surprise birthday party for mom on Sunday.",
    "Stay on the line please, I am transferring you to the billing department.",
    "Yes sir, transfer to the savings account of my brother, I will share the details on WhatsApp.",
    "Can you transfer 5000 rupees to my account for the rent? I will give it back on the first.",
    "The parcel from the customs warehouse needs a duty payment of 300 rupees, the courier will collect it at delivery.",
    "My cousin got arrested in a protest last year but the case was dismissed, he is fine now.",
    "We never ask for your OTP. Please share it only inside the official app.",
    # --- Hinglish
    "Bijli bill ka reminder: aapka August ka bill baaki hai, kripya jaldi bhar dijiye warna late fee lagegi.",
    "Aapki gas cylinder booking confirm hai, kal dopahar se pehle delivery ho jayegi.",
    "Yaad dilana tha ki aapki home loan EMI 18400 rupaye paanch tarikh ko kategi, account me balance rakhiye.",
    "Hello, main ek finance company se bol raha hoon, salaried logon ke liye personal loan offer hai. Interested hain?",
    "Aapka credit card payment do din se baaki hai, kripya turant bhar dijiye taaki extra charges na lagein.",
    "Bhaiya courier wala bol raha hoon, aapke phone par OTP gaya hoga, woh bata dijiye taaki parcel de sakun.",
    "Aapka Flipkart ka package aaj aayega, rider building ke bahar pahunch kar call karega.",
    "Yaar kal ka match dekha? Aakhri over zabardast tha.",
    "Aaj late ho jaunga, client ke saath meeting lambi chal gayi. Tum khana kha lena.",
    "Mera dost Jaipur me police me hai, usne bataya ki agle mahine se traffic rules kadak ho rahe hain.",
    "Kal TV par ek crime serial dekha jisme inspector banker ko fraud ke liye giraftar karta hai. Kaafi mast tha.",
    "Khabar aayi hai ki Hyderabad me police ne ek cyber fraud gang ko pakda hai.",
    "Humare ghar ke paas wale police station ki nayi building ban rahi hai, society meeting me yahi baat ho rahi thi.",
    "Mere vakeel ne bataya ki sunwai 12 tarikh ko hai, bas agli date milegi.",
    "Airport par customs ne sirf bag check kiye aur paanch minute me jaane diya.",
    "RBI ne digital lending apps ke naye niyam banaye hain, mere accountant ne samjhaya.",
    "RBI ka abhiyan hai ki kisi ko bhi apna UPI PIN kabhi share na karein.",
    "Pyare grahak, savdhan: thag bank adhikari banke call karte hain. OTP, CVV ya PIN kabhi share na karein.",
    "Koi bhi sarkari agency ya bank aapko safe account me paise transfer karne ko nahi kehta. Aisa koi kahe to yeh scam hai, cyber crime portal par report kijiye.",
    "Jan jagrukta: koi bhi officer video call par aapko arrest nahi kar sakta. Agar koi kahe to fraud hai, call kaatiye aur 1930 dial kijiye.",
    "Bank ki taraf se: hum aapko call karke PIN verify nahi karte aur na hi screen sharing app install karwate hain.",
    "Kripya app me apna email verify kijiye aur mobile number update kijiye taaki statements aate rahein.",
    "Aapke KYC documents safaltapurvak verify ho gaye hain aur account puri tarah active hai.",
    "Hello, bank se bol raha hoon, kya aapne aaj petrol pump par 3200 rupaye ka purchase kiya tha?",
    "Sir aapka passport police verification somvar ko hai, ek officer das se barah ke beech ghar aayenge.",
    "Aapka income tax refund process ho gaya hai, ek hafte me account me aa jayega.",
    "Driving licence renew karna hai, pata hai kya RTO ab online payment leta hai?",
    "Kya doctor kal shaam ko available hain? Papa ka blood pressure check karwana hai.",
    "Abhi baat nahi kar sakta, meeting me hoon. Ek ghante baad call karun?",
    "Badhai ho, aapko agle Budhvar interview ke liye shortlist kiya gaya hai.",
    "Aapki insurance policy renewal due hai, 30 tarikh se pehle bhar dijiye warna cover lapse ho jayega.",
    "Branch me account kholne aayen to Aadhaar aur PAN card saath rakhiye.",
    "Kisi ko mat batana, par main Sunday ko mummy ke liye surprise birthday party plan kar raha hoon.",
    "Line par bane rahiye, main aapko billing department se connect kar raha hoon.",
    "Bhai ke savings account me paise transfer kar dena, details WhatsApp par bhej dunga.",
    "Rent ke liye mere account me 5000 rupaye transfer kar do, pehli ko wapas kar dunga.",
    "Mera cousin pichhle saal ek protest me giraftar hua tha par case khaarij ho gaya, ab wo theek hai.",
    "Hum kabhi aapka OTP nahi mangte. Sirf official app ke andar hi share kijiye.",
    # --- Devanagari
    "बिजली बिल का रिमाइंडर: आपका अगस्त का बिल बाकी है, कृपया जल्दी भर दीजिए वरना लेट फीस लगेगी।",
    "आपकी गैस सिलेंडर बुकिंग कन्फर्म है, कल दोपहर से पहले डिलीवरी हो जाएगी।",
    "याद दिलाना था कि आपकी होम लोन ईएमआई 18400 रुपये पांच तारीख को कटेगी, खाते में बैलेंस रखिए।",
    "हेलो, मैं एक फाइनेंस कंपनी से बोल रहा हूँ, नौकरीपेशा लोगों के लिए पर्सनल लोन ऑफर है। इच्छुक हैं?",
    "आपका क्रेडिट कार्ड भुगतान दो दिन से बाकी है, कृपया तुरंत भर दीजिए ताकि अतिरिक्त शुल्क न लगे।",
    "भैया कूरियर वाला बोल रहा हूँ, आपके फोन पर ओटीपी गया होगा, वो बता दीजिए ताकि पार्सल दे सकूँ।",
    "आपका फ्लिपकार्ट का पैकेज आज आएगा, राइडर बिल्डिंग के बाहर पहुँचकर कॉल करेगा।",
    "यार कल का मैच देखा? आखिरी ओवर ज़बरदस्त था।",
    "आज देर से आऊँगा, क्लाइंट के साथ मीटिंग लंबी चल गई। तुम खाना खा लेना।",
    "मेरा दोस्त जयपुर में पुलिस में है, उसने बताया कि अगले महीने से ट्रैफिक नियम सख्त हो रहे हैं।",
    "कल टीवी पर एक क्राइम सीरियल देखा जिसमें इंस्पेक्टर बैंकर को धोखाधड़ी के लिए गिरफ्तार करता है। बहुत मज़ेदार था।",
    "खबर आई है कि हैदराबाद में पुलिस ने एक साइबर ठगी गिरोह को पकड़ा है।",
    "हमारे घर के पास वाले पुलिस थाने की नई बिल्डिंग बन रही है, सोसाइटी मीटिंग में यही बात हो रही थी।",
    "मेरे वकील ने बताया कि सुनवाई 12 तारीख को है, बस अगली तारीख मिलेगी।",
    "एयरपोर्ट पर कस्टम्स ने सिर्फ बैग चेक किए और पांच मिनट में जाने दिया।",
    "आरबीआई ने डिजिटल लेंडिंग ऐप्स के नए नियम बनाए हैं, मेरे अकाउंटेंट ने समझाया।",
    "आरबीआई का अभियान है कि किसी को भी अपना यूपीआई पिन कभी शेयर न करें।",
    "प्रिय ग्राहक, सावधान: ठग बैंक अधिकारी बनकर कॉल करते हैं। ओटीपी, सीवीवी या पिन कभी शेयर न करें।",
    "कोई भी सरकारी एजेंसी या बैंक आपको सेफ अकाउंट में पैसे ट्रांसफर करने को नहीं कहता। ऐसा कोई कहे तो यह स्कैम है, साइबर क्राइम पोर्टल पर रिपोर्ट कीजिए।",
    "जन जागरूकता: कोई भी अधिकारी वीडियो कॉल पर आपको गिरफ्तार नहीं कर सकता। कोई कहे तो यह धोखाधड़ी है, कॉल काटिए और 1930 डायल कीजिए।",
    "बैंक की ओर से: हम कॉल करके पिन वेरिफाई नहीं करते और न ही स्क्रीन शेयरिंग ऐप इंस्टॉल करवाते हैं।",
    "कृपया ऐप में अपना ईमेल वेरिफाई कीजिए और मोबाइल नंबर अपडेट कीजिए ताकि स्टेटमेंट आते रहें।",
    "आपके केवाईसी दस्तावेज़ सफलतापूर्वक वेरिफाई हो गए हैं और खाता पूरी तरह सक्रिय है।",
    "हेलो, बैंक से बोल रहा हूँ, क्या आपने आज पेट्रोल पंप पर 3200 रुपये की खरीदारी की थी?",
    "सर आपका पासपोर्ट पुलिस वेरिफिकेशन सोमवार को है, एक अधिकारी दस से बारह के बीच घर आएंगे।",
    "आपका इनकम टैक्स रिफंड प्रोसेस हो गया है, एक हफ्ते में खाते में आ जाएगा।",
    "ड्राइविंग लाइसेंस रिन्यू करना है, पता है क्या आरटीओ अब ऑनलाइन पेमेंट लेता है?",
    "क्या डॉक्टर कल शाम उपलब्ध हैं? पापा का ब्लड प्रेशर चेक करवाना है।",
    "अभी बात नहीं कर सकता, मीटिंग में हूँ। एक घंटे बाद कॉल करूँ?",
    "बधाई हो, आपको अगले बुधवार इंटरव्यू के लिए शॉर्टलिस्ट किया गया है।",
    "आपकी बीमा पॉलिसी का नवीनीकरण बाकी है, 30 तारीख से पहले भर दीजिए वरना कवर खत्म हो जाएगा।",
    "ब्रांच में खाता खोलने आएं तो आधार और पैन कार्ड साथ रखिए।",
    "किसी को मत बताना, पर मैं रविवार को मम्मी के लिए सरप्राइज़ बर्थडे पार्टी प्लान कर रहा हूँ।",
    "लाइन पर बने रहिए, मैं आपको बिलिंग विभाग से कनेक्ट कर रहा हूँ।",
    "भाई के सेविंग्स खाते में पैसे ट्रांसफर कर देना, डिटेल्स व्हाट्सऐप पर भेज दूँगा।",
    "किराए के लिए मेरे खाते में 5000 रुपये ट्रांसफर कर दो, पहली को लौटा दूँगा।",
    "मेरा चचेरा भाई पिछले साल एक प्रदर्शन में गिरफ्तार हुआ था पर केस खारिज हो गया, अब वो ठीक है।",
    "हम कभी आपसे ओटीपी नहीं मांगते। इसे सिर्फ आधिकारिक ऐप के अंदर ही शेयर कीजिए।",
]
