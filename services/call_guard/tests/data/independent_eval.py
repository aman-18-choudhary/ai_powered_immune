"""FROZEN independent evaluation set (authored before any tuning of the fix round).

Not used for classifier training or rule tuning. Do not edit to make tests pass.
Each call is (lang, [chunks]); lang is en / hi-Latn / hi.
"""

SCAM_CALLS: list[tuple[str, list[str]]] = [
    # ---- English
    (
        "en",
        [
            "Good afternoon, I am Sub-Inspector Malhotra from the Delhi cyber cell. A case has been registered against your Aadhaar number.",
            "Your Aadhaar was used to book a courier carrying banned substances. You are being treated as a suspect and you are under digital arrest from now.",
            "Do not hang up and do not mention this to anyone in your family.",
        ],
    ),
    (
        "en",
        [
            "This is the Narcotics Control Bureau. A parcel with your name and phone number was intercepted at Mumbai airport containing contraband.",
            "Please stay on this video call. If you speak to a lawyer or your bank, it will be treated as obstruction.",
            "You will need to move your savings to the RBI protected account so that we can verify they are clean.",
        ],
    ),
    (
        "en",
        [
            "Your mobile number is linked to illegal activity and TRAI will cancel it within two hours. I am transferring you to the officer from the cyber crime department.",
            "Sir, I am Officer Rane. Confirm your Aadhaar and PAN now, a warrant is ready in your name.",
        ],
    ),
    (
        "en",
        [
            "Hello, I am calling from the Enforcement Directorate regarding a money laundering file in which your bank account is named.",
            "To prove you are not involved, deposit your funds in the government verification account today. The amount will be returned after the audit.",
            "Keep this strictly between us and stay connected on the video call.",
        ],
    ),
    (
        "en",
        [
            "I represent the Supreme Court registry. A non-bailable warrant has been issued against you.",
            "You can avoid immediate arrest only if you pay a security deposit right now. Transfer the money to the account I will message you.",
            "Remain on the line until the payment is confirmed and tell nobody.",
        ],
    ),
    (
        "en",
        [
            "This is Customs, Chennai. We seized a package addressed to you with five fake passports and foreign currency.",
            "An FIR will be filed unless you cooperate. You must remain in front of the camera and not leave your room.",
            "We need to check your balance and savings. Show me your banking app on screen.",
        ],
    ),
    (
        "en",
        [
            "Inspector Yadav here from the crime branch. Your SIM card has been used to send threatening messages and we are placing you under digital arrest.",
            "Switch on your camera and do not disconnect until my senior officer clears you. Do not tell your neighbours or relatives.",
        ],
    ),
    (
        "en",
        [
            "Namaste, calling from the Central Bureau of Investigation. Your name has come up in a financial fraud investigation.",
            "Immediately transfer all your money into the safe RBI account for temporary custody during the investigation.",
            "This is confidential. If you inform anyone, your family will also face arrest.",
        ],
    ),
    (
        "en",
        [
            "I am DCP Rathod, Mumbai police. Your son has been detained in a drug case and we need a settlement amount right now to release him.",
            "Send the money to this UPI ID immediately and do not call anyone else about it.",
        ],
    ),
    (
        "en",
        [
            "Calling from the telecom regulator. Your number will be blocked tonight because of complaints of harassment calls.",
            "To keep it active, tell me your Aadhaar and PAN details and stay on the line while the officer verifies your account.",
            "Officer Kumar will place you under digital arrest if there is any delay.",
        ],
    ),
    (
        "en",
        [
            "I am an officer with the income tax and ED joint team. Black money was routed through your account.",
            "Open your net banking on the video call and show the balance, we must verify everything. Do not close the call.",
            "After checking, you will need to transfer the funds to the RBI verification account for safekeeping.",
        ],
    ),
    (
        "en",
        [
            "Sir this is the cyber police. A video of you has been found on an illegal website and a case is registered.",
            "You are under investigation and under digital arrest. Do not leave the video call even for a minute.",
            "Pay the clearance deposit now to close the case, and do not speak to anyone.",
        ],
    ),
    (
        "en",
        [
            "Hello, I'm calling from the anti-terror squad. Your phone number was found on a seized device.",
            "You are not allowed to disconnect. Stay in the room, keep your camera on, and share your bank balance so we can clear you.",
        ],
    ),
    (
        "en",
        [
            "This is the CBI. Your Aadhaar is linked to a human trafficking case with a bailable warrant already prepared.",
            "Put your funds in the safe account immediately to demonstrate good faith. Tell no one, this is a sealed case.",
        ],
    ),
    # ---- romanised Hindi
    (
        "hi-Latn",
        [
            "Namaste, main Delhi police cyber cell se Inspector Chauhan bol raha hoon. Aapke Aadhaar par ek serious case darj hua hai.",
            "Aapke naam se ek parcel pakda gaya hai jisme nashili cheezein mili hain. Aap abhi se digital arrest me hain.",
            "Video call band mat kijiye aur ghar walon ko kuch mat batayiye.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Hum Narcotics Bureau se bol rahe hain. Mumbai airport par aapke naam ka ek package pakda gaya hai.",
            "Aapko camera on rakhkar line par rehna hoga. Kisi vakeel ya bank se baat karna gunaah maana jayega.",
            "Apni saari bachat RBI ke protected account me transfer kijiye taki hum jaanch kar sakein.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Aapka mobile number TRAI do ghante me band kar dega kyunki isse galat calls ho rahi hain. Main aapko cyber crime officer se milata hoon.",
            "Sir main Officer Rane hoon, apna Aadhaar aur PAN abhi batayiye, aapke naam warrant nikal chuka hai.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Main ED office se bol raha hoon, ek money laundering file me aapke bank account ka naam aaya hai.",
            "Apni begunahi sabit karne ke liye aaj hi paise government verification account me jama kijiye, jaanch ke baad wapas honge.",
            "Yeh baat gopniya hai, kisi ko mat batana aur video call par bane rahiye.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Main Supreme Court registry se bol raha hoon. Aapke khilaf non-bailable warrant jaari hua hai.",
            "Giraftari se bachna hai to abhi security deposit bhejiye. Paise us account me bhej dijiye jo main message karunga.",
            "Payment hone tak line par rahiye aur kisi ko mat bataiye.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Chennai Customs se baat kar raha hoon. Aapke naam ka ek parcel mila hai jisme paanch nakli passport hain.",
            "Agar aap cooperate nahi karenge to FIR darj hogi. Camera ke saamne baithe rahiye, kamre se bahar mat jaiye.",
            "Apna bank balance aur banking app screen par dikhaiye, hume verify karna hai.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Crime branch se Inspector Yadav bol raha hoon. Aapke SIM se dhamki bhare messages gaye hain, aapko digital arrest kiya ja raha hai.",
            "Camera chalu rakhiye aur call disconnect mat kariye jab tak mere senior clear na karein. Padosiyon ya rishtedaron ko mat batana.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Namaste, CBI se bol raha hoon. Ek financial fraud ki jaanch me aapka naam saamne aaya hai.",
            "Turant apna saara paisa RBI ke safe account me transfer kijiye, jaanch tak woh temporary custody me rahega.",
            "Yeh confidential hai. Kisi ko bataya to parivar ko bhi giraftar karna padega.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Main DCP Rathod bol raha hoon Mumbai police se. Aapka beta drug case me pakda gaya hai, use chhudane ke liye abhi settlement ke paise chahiye.",
            "Yeh UPI ID par turant paise bhejiye aur kisi aur ko call mat kijiye.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Telecom authority se call hai. Aapka number aaj raat band ho jayega kyunki harassment calls ki complaint aayi hai.",
            "Number chalu rakhna hai to apna Aadhaar aur PAN batayiye aur line par rahiye jab tak officer aapka account verify karte hain.",
            "Der hui to Officer Kumar aapko digital arrest me daal denge.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Income tax aur ED ki joint team se bol raha hoon. Aapke account se black money ghumaya gaya hai.",
            "Video call par net banking kholkar balance dikhaiye, hume sab verify karna hai. Call mat kaatiye.",
            "Check ke baad aapko paise RBI verification account me transfer karne honge.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Sir cyber police se call hai. Ek illegal website par aapka video mila hai aur case register ho gaya hai.",
            "Aap investigation me hain aur digital arrest me hain. Ek minute ke liye bhi video call chhodiye mat.",
            "Case band karne ke liye abhi clearance deposit bhejiye aur kisi se baat mat kijiye.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Main anti-terror squad se bol raha hoon. Aapka phone number ek seized device me mila hai.",
            "Aap call disconnect nahi kar sakte. Kamre me rahiye, camera on rakhiye aur apna bank balance bataiye taki hum aapko clear kar sakein.",
        ],
    ),
    (
        "hi-Latn",
        [
            "Yeh CBI hai. Aapka Aadhaar human trafficking case se juda hai aur bailable warrant taiyaar hai.",
            "Apna paisa turant safe account me daliye. Kisi ko mat batana, yeh sealed case hai.",
        ],
    ),
    # ---- Devanagari
    (
        "hi",
        [
            "नमस्ते, मैं दिल्ली पुलिस साइबर सेल से इंस्पेक्टर चौहान बोल रहा हूँ। आपके आधार पर एक गंभीर मामला दर्ज हुआ है।",
            "आपके नाम से एक पार्सल पकड़ा गया है जिसमें प्रतिबंधित सामान मिला है। आप अभी से डिजिटल अरेस्ट में हैं।",
            "वीडियो कॉल बंद मत कीजिए और घरवालों को कुछ मत बताइए।",
        ],
    ),
    (
        "hi",
        [
            "हम नारकोटिक्स ब्यूरो से बोल रहे हैं। मुंबई एयरपोर्ट पर आपके नाम का एक पैकेज पकड़ा गया है।",
            "आपको कैमरा चालू रखकर लाइन पर रहना होगा। किसी वकील या बैंक से बात करना अपराध माना जाएगा।",
            "अपनी सारी बचत आरबीआई के सुरक्षित खाते में ट्रांसफर कीजिए ताकि हम जांच कर सकें।",
        ],
    ),
    (
        "hi",
        [
            "आपका मोबाइल नंबर ट्राई दो घंटे में बंद कर देगा क्योंकि इससे गलत कॉल हो रही हैं। मैं आपको साइबर क्राइम अधिकारी से मिलाता हूँ।",
            "सर मैं ऑफिसर राणे हूँ, अपना आधार और पैन अभी बताइए, आपके नाम वारंट निकल चुका है।",
        ],
    ),
    (
        "hi",
        [
            "मैं ईडी ऑफिस से बोल रहा हूँ, एक मनी लॉन्ड्रिंग फाइल में आपके बैंक खाते का नाम आया है।",
            "अपनी बेगुनाही साबित करने के लिए आज ही पैसे सरकारी वेरिफिकेशन खाते में जमा कीजिए, जांच के बाद वापस होंगे।",
            "यह बात गोपनीय है, किसी को मत बताना और वीडियो कॉल पर बने रहिए।",
        ],
    ),
    (
        "hi",
        [
            "मैं सुप्रीम कोर्ट रजिस्ट्री से बोल रहा हूँ। आपके खिलाफ गैर-जमानती वारंट जारी हुआ है।",
            "गिरफ्तारी से बचना है तो अभी सिक्योरिटी डिपॉजिट भेजिए। पैसे उस खाते में भेज दीजिए जो मैं मैसेज करूंगा।",
            "पेमेंट होने तक लाइन पर रहिए और किसी को मत बताइए।",
        ],
    ),
    (
        "hi",
        [
            "चेन्नई कस्टम्स से बात कर रहा हूँ। आपके नाम का एक पार्सल मिला है जिसमें पाँच नकली पासपोर्ट हैं।",
            "अगर आप सहयोग नहीं करेंगे तो एफआईआर दर्ज होगी। कैमरे के सामने बैठे रहिए, कमरे से बाहर मत जाइए।",
            "अपना बैंक बैलेंस और बैंकिंग ऐप स्क्रीन पर दिखाइए, हमें वेरिफाई करना है।",
        ],
    ),
    (
        "hi",
        [
            "क्राइम ब्रांच से इंस्पेक्टर यादव बोल रहा हूँ। आपके सिम से धमकी भरे मैसेज गए हैं, आपको डिजिटल अरेस्ट किया जा रहा है।",
            "कैमरा चालू रखिए और कॉल डिस्कनेक्ट मत कीजिए जब तक मेरे सीनियर क्लियर न करें। पड़ोसियों या रिश्तेदारों को मत बताना।",
        ],
    ),
    (
        "hi",
        [
            "नमस्ते, सीबीआई से बोल रहा हूँ। एक वित्तीय धोखाधड़ी की जांच में आपका नाम सामने आया है।",
            "तुरंत अपना सारा पैसा आरबीआई के सेफ अकाउंट में ट्रांसफर कीजिए, जांच तक वह अस्थायी हिरासत में रहेगा।",
            "यह गोपनीय है। किसी को बताया तो परिवार को भी गिरफ्तार करना पड़ेगा।",
        ],
    ),
    (
        "hi",
        [
            "मैं डीसीपी राठौड़ बोल रहा हूँ मुंबई पुलिस से। आपका बेटा ड्रग केस में पकड़ा गया है, उसे छुड़ाने के लिए अभी सेटलमेंट के पैसे चाहिए।",
            "इस यूपीआई आईडी पर तुरंत पैसे भेजिए और किसी और को कॉल मत कीजिए।",
        ],
    ),
    (
        "hi",
        [
            "टेलीकॉम अथॉरिटी से कॉल है। आपका नंबर आज रात बंद हो जाएगा क्योंकि उत्पीड़न कॉल की शिकायत आई है।",
            "नंबर चालू रखना है तो अपना आधार और पैन बताइए और लाइन पर रहिए जब तक अधिकारी आपका अकाउंट वेरिफाई करते हैं।",
            "देर हुई तो ऑफिसर कुमार आपको डिजिटल अरेस्ट में डाल देंगे।",
        ],
    ),
    (
        "hi",
        [
            "इनकम टैक्स और ईडी की संयुक्त टीम से बोल रहा हूँ। आपके खाते से काला धन घुमाया गया है।",
            "वीडियो कॉल पर नेट बैंकिंग खोलकर बैलेंस दिखाइए, हमें सब वेरिफाई करना है। कॉल मत काटिए।",
            "जांच के बाद आपको पैसे आरबीआई वेरिफिकेशन खाते में ट्रांसफर करने होंगे।",
        ],
    ),
    (
        "hi",
        [
            "सर साइबर पुलिस से कॉल है। एक अवैध वेबसाइट पर आपका वीडियो मिला है और केस दर्ज हो गया है।",
            "आप जांच में हैं और डिजिटल अरेस्ट में हैं। एक मिनट के लिए भी वीडियो कॉल मत छोड़िए।",
            "केस बंद करने के लिए अभी क्लियरेंस डिपॉजिट भेजिए और किसी से बात मत कीजिए।",
        ],
    ),
    (
        "hi",
        [
            "मैं आतंकवाद निरोधी दस्ते से बोल रहा हूँ। आपका फोन नंबर एक जब्त किए गए डिवाइस में मिला है।",
            "आप कॉल डिस्कनेक्ट नहीं कर सकते। कमरे में रहिए, कैमरा ऑन रखिए और अपना बैंक बैलेंस बताइए ताकि हम आपको क्लियर कर सकें।",
        ],
    ),
    (
        "hi",
        [
            "यह सीबीआई है। आपका आधार मानव तस्करी के केस से जुड़ा है और जमानती वारंट तैयार है।",
            "अपना पैसा तुरंत सेफ अकाउंट में डालिए। किसी को मत बताना, यह सीलबंद केस है।",
        ],
    ),
]

# Benign calls; `hard` marks calls that deliberately use scam vocabulary in a safe context.
BENIGN_CALLS: list[tuple[str, list[str], bool]] = [
    # ---- hard negatives (en)
    (
        "en",
        [
            "Hello, this is Axis Bank fraud prevention. We are reminding all customers that bank staff will never request your OTP or card PIN on a call."
        ],
        True,
    ),
    (
        "en",
        [
            "Public notice from the cyber cell: scammers pose as police or CBI and claim you are in digital arrest. It is not real, simply hang up and call 1930."
        ],
        True,
    ),
    (
        "en",
        [
            "Hi Karan, it's Vikram. I'm a police constable now, posted at the Andheri station. Let's catch up over chai this weekend."
        ],
        True,
    ),
    (
        "en",
        [
            "I read an article about the RBI changing the repo rate. My home loan EMI might go down a little next month."
        ],
        True,
    ),
    (
        "en",
        [
            "Good evening, calling from the electricity board. Your bill of rupees 1800 is overdue, kindly pay today to avoid a disconnection charge."
        ],
        True,
    ),
    (
        "en",
        [
            "We are making a documentary on cyber crime. Our episode explains how fake officers threaten people with arrest on video calls."
        ],
        True,
    ),
    (
        "en",
        [
            "Hello, I'm the courier delivery executive. You will get an OTP on your phone, please tell it to me at the door so I can hand over the package."
        ],
        True,
    ),
    (
        "en",
        [
            "Dad, I have a customs clearance appointment at the airport tomorrow for my laptop. They check the bill and then let you go."
        ],
        True,
    ),
    (
        "en",
        [
            "Your court date for the traffic challan is on the 20th. The advocate says you only need to pay the fine and there is no arrest involved."
        ],
        True,
    ),
    (
        "en",
        [
            "Dear customer, to protect your account, never share your net banking password with anyone. If a caller says he is from the RBI, do not trust him."
        ],
        True,
    ),
    (
        "en",
        [
            "Ma'am, this is the passport office. Your police verification is complete and the passport will be dispatched in three days."
        ],
        True,
    ),
    # ---- hard negatives (hi-Latn)
    (
        "hi-Latn",
        [
            "Namaste, Axis Bank ki taraf se suchna: bank ka koi bhi karmchari aapse call par OTP ya PIN kabhi nahi mangta, savdhan rahiye."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Cyber cell ki chetavni: thag khud ko police ya CBI batakar digital arrest ka dar dikhate hain. Yeh sab jhooth hai, call kaatkar 1930 par report kijiye."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Arre Karan, main Vikram. Ab main police me constable hoon, Andheri thane me posting hai. Is weekend chai par milte hain."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Maine padha ki RBI ne repo rate me badlav kiya hai. Shayad agle mahine meri home loan ki EMI thodi kam ho jaye."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Bijli vibhag se call hai. Aapka 1800 rupaye ka bill baaki hai, kripya aaj bhar dijiye warna connection kat sakta hai."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Hum cyber crime par documentary bana rahe hain. Is episode me dikhaya hai ki nakli afsar video call par giraftari ka dar dikhate hain."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Bhaiya courier wala bol raha hoon. Aapke phone par OTP aayega, darwaze par mujhe bata dijiye taaki package de sakun."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Papa, kal airport par laptop ke liye customs clearance hai. Bill dikhate hain aur phir jaane dete hain."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Aapki challan ki court date 20 tareekh hai. Vakeel ne kaha sirf fine bharna hai, giraftari ka koi khatra nahi."
        ],
        True,
    ),
    (
        "hi-Latn",
        [
            "Pyare grahak, apna net banking password kisi ke saath share mat kijiye. Agar koi khud ko RBI se bataye to uspar bharosa na karein."
        ],
        True,
    ),
    # ---- hard negatives (hi)
    (
        "hi",
        [
            "प्रिय ग्राहक, एक्सिस बैंक की सूचना: बैंक का कोई भी कर्मचारी कॉल पर आपसे ओटीपी या पिन कभी नहीं मांगता, सावधान रहें।"
        ],
        True,
    ),
    (
        "hi",
        [
            "साइबर सेल की चेतावनी: ठग खुद को पुलिस या सीबीआई बताकर डिजिटल अरेस्ट का डर दिखाते हैं। यह सब झूठ है, कॉल काटकर 1930 पर रिपोर्ट करें।"
        ],
        True,
    ),
    (
        "hi",
        ["अरे करण, मैं विक्रम। अब मैं पुलिस में कांस्टेबल हूँ, अंधेरी थाने में पोस्टिंग है। इस वीकेंड चाय पर मिलते हैं।"],
        True,
    ),
    (
        "hi",
        [
            "बिजली विभाग से कॉल है। आपका 1800 रुपये का बिल बाकी है, कृपया आज भर दीजिए वरना कनेक्शन कट सकता है।"
        ],
        True,
    ),
    # ---- ordinary benign
    (
        "en",
        [
            "Hi, calling from HDFC Bank to confirm whether you made a payment of 2500 rupees at Big Bazaar today.",
            "Yes that was me.",
            "Thank you, the transaction is confirmed.",
        ],
        False,
    ),
    (
        "en",
        [
            "Hello sir, your Swiggy order is on the way and the delivery partner will reach in ten minutes.",
            "Okay, I'll be at the gate.",
        ],
        False,
    ),
    (
        "en",
        [
            "Hey, are we still meeting for lunch tomorrow? I was thinking about the new Italian place near your office."
        ],
        False,
    ),
    (
        "en",
        [
            "This is a reminder that your insurance premium of 12000 rupees is due on the 28th. You can pay through the app."
        ],
        False,
    ),
    (
        "en",
        [
            "Good morning, this is the school office. The parent teacher meeting is on Saturday at ten."
        ],
        False,
    ),
    (
        "en",
        [
            "I am calling about your pre-approved credit card limit upgrade. Would you like me to email the details?"
        ],
        False,
    ),
    (
        "en",
        [
            "Congratulations, your KYC has been successfully updated. You do not need to visit the branch."
        ],
        False,
    ),
    (
        "en",
        ["Dr. Mehta's clinic here, confirming your appointment for tomorrow at five pm."],
        False,
    ),
    (
        "en",
        [
            "Please keep your original documents ready when you come to the branch for the home loan signing."
        ],
        False,
    ),
    (
        "hi-Latn",
        [
            "Namaste, HDFC Bank se bol raha hoon. Kya aapne aaj Big Bazaar me 2500 rupaye ka payment kiya tha?",
            "Haan woh main hi tha.",
            "Dhanyawad, transaction confirm ho gaya.",
        ],
        False,
    ),
    (
        "hi-Latn",
        ["Sir aapka Swiggy order raaste me hai, delivery partner das minute me pahunchega."],
        False,
    ),
    (
        "hi-Latn",
        [
            "Kal lunch par milna hai na? Socha tha office ke paas wale naye Italian restaurant me chalein."
        ],
        False,
    ),
    (
        "hi-Latn",
        [
            "Yaad dilana tha ki aapke insurance premium ke 12000 rupaye 28 tareekh ko due hain. App se bhar sakte hain."
        ],
        False,
    ),
    (
        "hi-Latn",
        ["Namaste, school office se call hai. Shanivar subah das baje parent teacher meeting hai."],
        False,
    ),
    (
        "hi-Latn",
        [
            "Aapke credit card ki limit upgrade ka pre-approved offer hai. Kya main details email kar doon?"
        ],
        False,
    ),
    (
        "hi",
        [
            "नमस्ते, एचडीएफसी बैंक से बोल रहा हूँ। क्या आपने आज बिग बाज़ार में 2500 रुपये का भुगतान किया था?",
            "हाँ वह मैं ही था।",
            "धन्यवाद, लेन-देन की पुष्टि हो गई।",
        ],
        False,
    ),
    ("hi", ["सर आपका स्विगी ऑर्डर रास्ते में है, डिलीवरी पार्टनर दस मिनट में पहुँचेगा।"], False),
    ("hi", ["कल लंच पर मिलना है ना? सोचा था ऑफिस के पास वाले नए इटैलियन रेस्टोरेंट में चलें।"], False),
    (
        "hi",
        ["याद दिलाना था कि आपके बीमा प्रीमियम के 12000 रुपये 28 तारीख को देय हैं। ऐप से भर सकते हैं।"],
        False,
    ),
    ("hi", ["नमस्ते, स्कूल ऑफिस से कॉल है। शनिवार सुबह दस बजे पैरेंट टीचर मीटिंग है।"], False),
]
