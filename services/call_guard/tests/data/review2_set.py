"""DEV set 2: second reviewer probe (35 scam calls, 36 benign calls); no longer independent."""

SCAM = [
    # English
    [
        "Namaste sir, am I speaking with Mr. Kulkarni? I am calling from the Mumbai Cyber Police regarding your SIM card.",
        "We received multiple complaints that obscene messages were sent from your number. A case file is open against you.",
        "You cannot disconnect this call till the investigation is complete. Do not inform your wife or colleagues, it would jeopardise the probe.",
    ],
    [
        "Hello, this is Inspector Verma, Crime Branch. Your name has come up in a hawala racket.",
        "To avoid your immediate detention, you must move all your money into a secure custody account that I will give you, the RBI will refund it after clearance.",
    ],
    [
        "Good morning ma'am, I'm an officer with the Central Narcotics unit. A package addressed to you at the FedEx hub was found with 140 grams of drugs.",
        "Do not panic, but you are now under investigation and must remain on video until my senior takes your statement. Sorry for the inconvenience, this is procedure.",
        "Never speak about this to anyone outside, it is a confidential matter, otherwise we will have to send a team to your house.",
    ],
    [
        "Sir this is the Telecom Regulatory Authority. Your mobile will be permanently blocked in two hours because of illegal advertisement calls from it.",
        "Press 1 to speak to the cyber crime officer. I am connecting you now.",
        "Officer here. Your Aadhaar is linked to a fraud case in Hyderabad. Stay on the line and show me your bank balance on the app so I can check you are not involved.",
    ],
    [
        "Your account is flagged by the Supreme Court money laundering bench. I am the court registrar speaking to you.",
        "Fraud is a serious offence, sir. You can ignore this notice only at your own risk. We need you to deposit 2 lakh rupees as security bail amount today, it will be refunded.",
    ],
    [
        "This is Deputy Commissioner Rao. We have a warrant for your arrest in a human trafficking case using your PAN card.",
        "Report to the video room set up by us. Keep your camera on and do not leave the room or talk to anyone until we say so.",
    ],
    [
        "Hi, I am calling from Customs at Delhi airport. A parcel from Dubai in your name contains fake passports.",
        "Sorry sir, but this is a serious matter. To clear your name you must pay a clearance fine through UPI right now, else police will arrive at your home.",
    ],
    [
        "Hello, I am Agent Sharma from the Enforcement Directorate. Your bank accounts are being used for laundering black money.",
        "We are placing you under digital arrest effective immediately. Please do not hang up, you must keep the video call running for the next 48 hours.",
    ],
    [
        "I am Senior Inspector Das from the CBI economic offences wing. Report this call to nobody, it is a sealed matter.",
        "Your savings must be verified by the RBI. Transfer the balance to the government safe wallet number I'm sharing, we will credit it back once verified.",
    ],
    [
        "Madam, I am calling from the income tax investigation unit. There is a case of tax evasion in your name.",
        "Your account will be frozen unless you transfer the money into the department's temporary verification account. Do not discuss with your chartered accountant, he may be involved.",
    ],
    [
        "This is the Cyber Crime Cell, Bengaluru. We have intercepted a video of you on a fraud website.",
        "Under section 66 you will be arrested. However if you cooperate, we can place you in digital custody at home. Keep your phone camera pointed at you at all times.",
    ],
    [
        "Hello, I am an officer from the Mumbai police. Your son has been arrested in an accident case.",
        "If you want to settle it without a case, send fifty thousand to this UPI id right now. Don't tell his mother, it will make things worse.",
    ],
    [
        "Sir, you have a pending court notice. I'm a clerk from the High Court registry.",
        "A non-bailable warrant has been issued. To cancel it you have to share your net banking OTP for the security deposit, immediately.",
    ],
    [
        "Greetings. This is the Anti Terror Squad. A SIM bought with your Aadhaar was used in a terror funding case.",
        "You are suspect number one. Remain in front of the camera. You are not permitted to speak to your family or lawyer. We will verify all your funds in an RBI holding account.",
    ],
    # Hinglish romanised
    [
        "Namaste, main Inspector Yadav bol raha hoon Delhi Police se. Aapke naam se ek parcel pakda gaya hai jisme nashili dawaiyan hain.",
        "Aap abhi digital arrest me hain. Video call par bane rahiye, kisi ko kuch mat bataiye.",
    ],
    [
        "Sir, main CBI officer Khanna bol raha hu. Aapke Aadhaar se money laundering ka case darj hua hai.",
        "Apne saare paise RBI ke safe account me transfer kijiye, jaanch ke baad wapas mil jayenge. Call disconnect mat karna.",
    ],
    [
        "Hello madam, aapka number TRAI ki taraf se band hone wala hai kyunki isse illegal messages gaye hain.",
        "Main aapko cyber crime wale officer se connect karta hoon. Unhe apna Aadhaar aur PAN abhi batayiye, warna FIR ho jayegi.",
    ],
    [
        "Main Customs department se bol rahi hoon. Aapke naam ka courier Thailand se aaya hai jisme passport aur drugs mile hain.",
        "Mumbai police aapke ghar aane wali hai. Isse bachna hai to abhi settlement ke liye paisa UPI se bhejiye. Kisi ko mat batana.",
    ],
    [
        "Aapke khilaaf Supreme Court ka arrest warrant nikla hai. Hum aapko online custody me rakh rahe hain.",
        "Camera on rakhiye aur kamre se bahar mat jaiye. Aapka account verify karne ke liye balance dikhaiye.",
    ],
    [
        "Sir ye ED ki taraf se call hai. Aapke bank account se hawala transaction hua hai, case number 4421 darj hai.",
        "Safe account me paise daliye warna aapko giraft kar liya jayega. Ghar walon ko bilkul mat batana.",
    ],
    [
        "Hello, main Delhi cyber cell se SI Rathore bol raha hoon. Aapke SIM se dhamki bhare messages bheje gaye hain.",
        "Aap line par bane rahiye aur apna bank balance batayiye. Hum verification ke baad aapko clear kar denge. Sorry sir, ye zaruri hai.",
    ],
    [
        "Madam aapke beta ko police ne pakda hai, ek ladki ke case me. Main thane se bol raha hoon.",
        "Case band karwane ke liye 80 hazaar abhi bhejiye. Aapko kisi ko bhi phone nahi karna hai, warna FIR ho jayegi.",
    ],
    # Devanagari
    [
        "नमस्ते, मैं मुंबई क्राइम ब्रांच से इंस्पेक्टर पाटिल बोल रहा हूँ। आपके आधार से एक पार्सल बुक हुआ है जिसमें ड्रग्स मिले हैं।",
        "आप अभी डिजिटल अरेस्ट में हैं। वीडियो कॉल पर बने रहिए और किसी को कुछ मत बताइए।",
    ],
    [
        "हैलो, मैं सीबीआई अधिकारी बोल रहा हूँ। आपके खाते में मनी लॉन्ड्रिंग का केस दर्ज हुआ है।",
        "अपने सारे पैसे आरबीआई के सेफ अकाउंट में ट्रांसफर कीजिए, जांच के बाद वापस मिल जाएंगे। कॉल मत काटिए।",
    ],
    [
        "आपका मोबाइल नंबर ट्राई द्वारा दो घंटे में बंद कर दिया जाएगा क्योंकि उससे अवैध कॉल हुई हैं।",
        "मैं आपको साइबर क्राइम अधिकारी से जोड़ रहा हूँ। अपना आधार और पैन अभी बताइए, वरना वारंट निकलेगा।",
    ],
    [
        "मैं कस्टम विभाग से बोल रही हूँ। आपके नाम का एक कूरियर पकड़ा गया है जिसमें नकली पासपोर्ट हैं।",
        "गिरफ्तारी से बचना है तो अभी यूपीआई से जुर्माना जमा कीजिए। यह बात परिवार में किसी को मत बताना।",
    ],
    [
        "आपके खिलाफ सुप्रीम कोर्ट का गिरफ्तारी वारंट जारी हुआ है। हम आपको ऑनलाइन हिरासत में रख रहे हैं।",
        "कैमरा चालू रखिए और कमरे से बाहर मत जाइए। अपने खाते का बैलेंस दिखाइए ताकि हम वेरिफाई कर सकें।",
    ],
    # weak-first-contact / code-mixed / evasive
    [
        "Hello sir, a very important call regarding your mobile number, please hold for the officer.",
        "Officer speaking from cyber police. Your number is being used for illegal activity, Hyderabad FIR registered. Stay on call and do not tell anyone, sir.",
    ],
    [
        "Hi, this is a courtesy call from the delivery service about a package that is on hold.",
        "The package has been seized by customs with illegal items inside, a police case will be filed against you. Speak to officer Ravi who will take you under digital arrest if you do not cooperate.",
        "Ravi here, keep video on, do not tell anyone, and move your funds to the verification account right now.",
    ],
    [
        "Sir, it's regarding your Aadhaar. I am from UIDAI and the police. Aapka Aadhaar misuse hua hai.",
        "Police case banega. Aap ghabraiye mat, but ek verification account me paise transfer karne honge, don't tell anyone in family, it's confidential.",
    ],
    [
        "Hello I'm calling from your bank's fraud department. Your account has suspicious transactions linked to a money laundering case.",
        "The Mumbai police crime branch will call you now. Do not disconnect. They will ask you to move your money to an RBI safe account for protection, and you must follow their orders.",
    ],
    [
        "This is Special Officer Gupta. Ignore any message you have seen, I'm the real police. We never ask for money, but in your case you must deposit a refundable security amount in the government account.",
        "You are under digital arrest, stay on video and do not tell your family.",
    ],
    [
        "We are very sorry to inform you that a warrant is out under your name by the Mumbai Police. Sorry for the trouble, sir.",
        "Please treat this as an official order: you are in digital arrest, don't hang up the video call, and share your bank balance with the officer.",
    ],
    [
        "Sir, NCB officer speaking. We never harass innocent people. But a parcel in your name has narcotics. Do not report this call, it's an ongoing sealed investigation.",
        "To prove innocence you must send your savings to the RBI verification account. Stay on the video call until done.",
    ],
    [
        "This is an official message from the Police. Fraud has been committed using your Aadhaar. Report to the video call immediately.",
        "I am Inspector Joshi. Your account will be seized. Transfer 3 lakh rupees to a safe account today, don't disconnect, don't tell anyone.",
    ],
]
BENIGN = [
    # hard negatives: awareness, news, police etc
    (
        "hard",
        [
            "Dear customer, this is an alert from our bank. Fraudsters are calling people and posing as police officers. Please do not share your OTP and report suspicious calls on the national cybercrime helpline."
        ],
    ),
    (
        "hard",
        [
            "Hi dad, did you see the news last night? An old man in Pune lost twenty lakhs to a digital arrest scam, the caller pretended to be from the CBI.",
            "Yes beta, I saw it. Tell your mother never to pick up such video calls, it's all fake, the police can't arrest anyone on WhatsApp.",
        ],
    ),
    (
        "hard",
        [
            "Hello, this is your bank. We would like to remind you to complete your KYC before the end of the month.",
            "You can update your documents at the nearest branch or in the mobile app. Thank you for banking with us.",
        ],
    ),
    (
        "hard",
        [
            "Good evening, this is a reminder that your loan EMI of eight thousand rupees is due on the fifth.",
            "If you have already paid, please ignore this message. For questions call our customer care.",
        ],
    ),
    (
        "hard",
        [
            "Your electricity bill for September is generated and the last date for payment is the 15th. Pay on the app to avoid a late fee."
        ],
    ),
    (
        "hard",
        [
            "Hello, I am the delivery executive from the courier company. I am outside your gate with your parcel.",
            "Please tell me the OTP you received so that I can hand over the package.",
        ],
    ),
    (
        "hard",
        [
            "Hi, I'm calling about our new term life insurance plan. It covers up to one crore and the premium starts at ten thousand a year.",
            "Would you like me to send you the brochure on WhatsApp? There is no obligation at all.",
        ],
    ),
    (
        "hard",
        [
            "Good morning, this is City Hospital reception. Your appointment with Dr. Mehta is tomorrow at ten. Please bring your previous reports and arrive fifteen minutes early."
        ],
    ),
    (
        "hard",
        [
            "Dear parent, the school fee for the second term is due by the tenth of this month. You can pay by cheque or the online portal. Late payments attract a small fine."
        ],
    ),
    (
        "hard",
        [
            "I went to the police station today to renew my passport verification. The inspector there was very helpful and it was done in an hour."
        ],
    ),
    (
        "hard",
        [
            "My friend is a customs officer at the airport. He says the new baggage rules are really strict this year, you have to declare gold above a limit."
        ],
    ),
    (
        "hard",
        [
            "Court hearing for my property dispute got postponed again. My lawyer says the judge will give a new date next month, nothing to worry about."
        ],
    ),
    (
        "hard",
        [
            "Sir, this is the Income Tax helpdesk reminder. The last date for filing your return is the thirty first. You can file online through the official portal; we never ask for your password by phone."
        ],
    ),
    (
        "hard",
        [
            "Reminder from your bank: do not click on links in SMS asking you to update your account. Scammers are posing as bank officials. If in doubt, visit your branch."
        ],
    ),
    (
        "hard",
        [
            "Did you watch that crime show last night? The inspector put the villain under arrest after the courier with drugs was found, super twist."
        ],
    ),
    (
        "hard",
        [
            "Namaste, hum aapke bank se bol rahe hain. Aapka KYC update abhi baaki hai, kripya apne nazdiki branch jaakar document jama karein."
        ],
    ),
    (
        "hard",
        [
            "Aapka bijli ka bill is mahine ka 1450 rupaye hai, aakhri tarikh 20 hai. Late fee se bachne ke liye time par bhugtan kijiye."
        ],
    ),
    (
        "hard",
        [
            "Yaar kal news me dekha, ek aadmi ko digital arrest scam me do lakh ka chuna laga. Mummy ko bol dena ki aise calls pe bharosa na kare, police aise video call par arrest nahi karti."
        ],
    ),
    (
        "hard",
        [
            "Hello sir, main courier boy bol raha hoon, aapka parcel gate par hai. Delivery ka OTP bata dijiye please."
        ],
    ),
    (
        "hard",
        [
            "Namaste, aapki beti ke school ki fees ka reminder hai, 10 tarikh tak jama karni hai, uske baad late charge lagega."
        ],
    ),
    (
        "hard",
        [
            "बैंक की ओर से सूचना: ग्राहकों को सावधान किया जाता है कि पुलिस या सीबीआई बनकर आने वाले फोन पर कोई जानकारी साझा न करें। साइबर अपराध की शिकायत 1930 पर करें।"
        ],
    ),
    ("hard", ["आपका लोन ईएमआई ₹7,500 इस महीने की पांच तारीख को देय है। कृपया समय पर भुगतान करें।"]),
    (
        "hard",
        [
            "कल अख़बार में पढ़ा कि एक बुज़ुर्ग को फ़र्ज़ी पुलिस वालों ने ठग लिया। हमें भी सावधान रहना चाहिए और अनजान कॉल पर ओटीपी नहीं देना चाहिए।"
        ],
    ),
    (
        "hard",
        [
            "Hello, I am calling from Axis customer care. Your debit card ending 4421 has been dispatched. You will receive it in three working days. No action is needed from you."
        ],
    ),
    (
        "hard",
        [
            "The cyber cell conducted an awareness session at our office today. They told us that no officer will ever ask you to stay on a video call or move money to a safe account."
        ],
    ),
    (
        "hard",
        [
            "Police verification for the new tenant is done. The inspector said we just need to submit the rent agreement and Aadhaar copy at the station tomorrow."
        ],
    ),
    (
        "hard",
        [
            "Your parcel is held at the customs warehouse. Please pay the pending customs duty of 450 rupees on the official website to get it released."
        ],
    ),
    (
        "hard",
        [
            "Ma'am, this is the telecom operator. Your postpaid bill is overdue, the number will be barred if not paid by Friday. Please recharge on the app."
        ],
    ),
    # plain benign
    ("easy", ["Hey, are you coming for dinner on Saturday? Mom is making biryani."]),
    ("easy", ["Hello, can I book a table for four at eight tonight? Near the window if possible."]),
    ("easy", ["Sir your cab is arriving in two minutes, please be ready at the pickup point."]),
    (
        "easy",
        ["Bhai kal match dekhne chalega? Main tickets le leta hoon, tu bas time par pahunch jana."],
    ),
    (
        "easy",
        [
            "Hi, I'm calling from the gym to say that your membership expires next week, you can renew at the desk."
        ],
    ),
    ("easy", ["नमस्ते, आपकी गैस सिलेंडर की बुकिंग हो गई है, कल सुबह तक डिलीवरी हो जाएगी।"]),
    (
        "easy",
        [
            "Hello, this is your internet provider. We will have a scheduled maintenance tonight from midnight to two. Service may be briefly unavailable."
        ],
    ),
    (
        "easy",
        [
            "Mr. Nair, your visa appointment is confirmed for Monday at 9 am at the consulate. Please carry your original passport."
        ],
    ),
]
