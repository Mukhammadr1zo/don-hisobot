# Don mahsulotlarini kunlik tushirish hisoboti: dizayn

Sana: 2026-10-12. Holat: tasdiqlangan.

## 1. Maqsad

Hozir «Дон маҳсулотларини кунлик тушириш бўйича маълумот» jadvali stansiyalardan qo'lda yig'iladi.
Tizim buni almashtiradi: stansiya ma'lumotni o'zi kiritadi, MTU o'z stansiyalarini, Yuk va tijorat
boshqarmasi hamma MTU ni ko'radi.

Jadval tuzilishi (bir stansiya, bir sutka):

| Davr | Ustunlar |
|---|---|
| 20:00–08:00 | туширилган, кириб келган, юк туширишда қолган вагонлар сони |
| 08:00–20:00 | xuddi shu uchta |
| Sutka | жами туширилган (ikki davr yig'indisi) |

Tashkilot: 1 boshqarma → 6 MTU (Toshkent, Qo'qon, Buxoro, Qo'ng'irot, Qarshi, Termiz) → stansiyalar (50–200).

## 2. Qabul qilingan qarorlar

1. Akkauntlar: 1 ta boshqarma, 6 ta MTU. Stansiyalarga shaxsiy profil ochilmaydi.
2. Stansiyalar Excel'dan import qilinadi, har biriga tizim PIN yaratadi. Stansiya: havola → MTU → stansiya → PIN.
3. Tizim internetda turadi, stansiyalar telefon va kompyuterdan kiradi.
4. Qoldiq avtomatik hisoblanadi, stansiya qo'lda o'zgartira oladi, o'zgartirilgani belgilanib ko'rinadi.
5. Stansiya sutkada 2 marta kiritadi: 08:00 dan keyin tungi, 20:00 dan keyin kunduzgi davr.
6. Stansiya davr tugagach 24 soat ichida tuzata oladi, keyin faqat o'z MTU si yoki boshqarma. Har o'zgarish tarixda qoladi.
7. Hisobotlar: Excel eksport (hozirgi shablon ko'rinishida), kiritmaganlar ro'yxati, davr bo'yicha yig'ma, grafiklar.
8. Interfeys tili: o'zbek kirill, bitta til.
9. Sutka D = (D−1) 20:00 → D 20:00. Tungi davr (D−1) 20:00 → D 08:00, kunduzgi davr D 08:00 → D 20:00.

## 3. Texnologiya

- Django 5.2 LTS, Python 3.12 (uv), PostgreSQL 17, serverda yig'iladigan shablonlar.
- Python paketlari: `django`, `psycopg[binary]`, `openpyxl`, `defusedxml`, `gunicorn`, `whitenoise`.
- Statik fayllar loyiha ichida: bitta `app.css`, Chart.js. Tashqi CDN ishlatilmaydi.
- SPA, REST API, Node build, Redis, Celery, Django admin yo'q.
- Bitta Django ilova (`hisobot`). Vaqt mantig'i faqat `periods.py` da, `entry` jadvaliga faqat `save_entry()` yozadi.
- Vaqt zonasi `Asia/Tashkent`. "Hozir" har doim `timezone.now()`; `date.today()` ishlatilmaydi.

## 4. Ma'lumotlar modeli

| Jadval | Ustunlar | Cheklovlar |
|---|---|---|
| `mtu` | name, sort | name noyob; 6 qator seed qilinadi |
| `user` (AbstractUser) | + mtu (NULL bo'lishi mumkin) | mtu NULL = boshqarma |
| `station` | mtu, name, sort, active_from, active_to, opening_remaining, opening_at, pin_hash, pin_version, last_login_at, fail_streak, locked_until | (mtu, lower(name)) noyob; name ≤ 100 belgi va `= + - @` bilan boshlanmaydi; opening_remaining ≥ 0; hech qachon o'chirilmaydi |
| `entry` | station, period_end, unloaded, arrived, remaining_calc, remaining_override, version, created_at, updated_at | (station, period_end) noyob; unloaded va arrived 0–999; override 0–9999 yoki NULL; period_end faqat 08:00 yoki 20:00 (Toshkent); remaining_calc manfiy bo'lishi mumkin |
| `change` | at, kind, station, entry, user, ip, cause, old, new | kind: entry / opening / station / pin / unlock; user NULL = stansiya (PIN orqali); UPDATE va DELETE trigger bilan taqiqlangan |
| `login_failure` | at, ip, station, username | 7 kundan eski qatorlar yangi yozuvda tozalanadi |

- Amaldagi qoldiq = `remaining_override`, u bo'lmasa `remaining_calc`. Hisobot sanasi, davr raqami va sutka jami saqlanmaydi, hisoblab chiqariladi.
- `entry` qatorlari o'chirilmaydi (trigger).
- Davr stansiyadan **kutiladi**, agar `active_from < period_end` va (`active_to` NULL yoki `period_end ≤ active_to`). O'chirish `active_to = hozir` qiladi, qayta yoqish `active_from = hozir` qilib `active_to` ni tozalaydi.

## 5. Qoldiq algoritmi

1. **Zanjir.** Stansiya yozuvlari `period_end` bo'yicha tartiblanadi. `calc = prev + arrived − unloaded`; `prev` eng yaqin oldingi yozuvning amaldagi qoldig'i, u bo'lmasa `opening_remaining`.
2. **Boshlang'ich qoldiq.** Stansiyaning birinchi yozuvi formasi «Давр бошидаги қолдиқ» ni ham so'raydi. U `opening_at` = shu davr boshi bilan saqlanadi, `active_from` ham shu vaqtga qo'yiladi. `opening_at` dan oldingi davrga yozuv kiritilmaydi. Keyin uni faqat MTU yoki boshqarma o'zgartiradi; o'zgarish tarixga yoziladi va zanjir qayta hisoblanadi.
3. **O'tkazib yuborilgan davr.** Qator yaratilmaydi, nol qo'yilmaydi. Keyingi yozuv eng yaqin oldingisidan davom etadi, forma «Олдинги давр киритилмаган» deb ogohlantiradi. Bo'shliq keyin to'ldirilsa, zanjir qayta hisoblanadi.
4. **Qo'lda kiritish** "sanab chiqilgan" degani. Qiymat kiritilgandek saqlanadi (calc ga teng bo'lsa ham), amaldagi qoldiq bo'ladi va zanjirning yangi tayanch nuqtasi hisoblanadi. Belgi: `override ≠ calc`. Maydon bo'sh qoldirilsa, qo'lda kiritish bekor bo'ladi.
5. **To'g'ridan-to'g'ri saqlash.** Amaldagi qoldiq ≥ 0 bo'lishi shart, aks holda saqlanmaydi (raqamlarni tuzatish yoki qo'lda kiritish kerak).
6. **Saqlash**, bitta tranzaksiya:
   1. Stansiya qatoriga `SELECT … FOR UPDATE`.
   2. Tekshiruv: foydalanuvchi doirasi, stansiya faolligi, vaqt oynasi (6-bo'lim), `opening_at`.
   3. Bazadagi yozuv yuborilgan qiymatlarga teng bo'lsa: muvaffaqiyat qaytariladi, hech narsa yozilmaydi.
   4. Bazadagi `version` formadagidan farq qilsa (yangi yozuv uchun 0), yoki hozirgi `prev` formada ko'rsatilgan `prev` dan farq qilsa: hech narsa yozilmaydi, forma yangi raqamlar bilan qayta ko'rsatiladi.
   5. Yozuv saqlanadi (`version + 1`) va eski hamda yangi qiymatlar bilan `change` qatori yoziladi.
   6. Kaskad: keyingi yozuvlar tartib bilan qayta hisoblanadi. `calc` o'zgarmasa to'xtaydi. O'zgarsa yangilanadi (`version + 1`), `cause` ko'rsatilgan `change` qatori yoziladi; qo'lda kiritilgan yozuvdan keyin to'xtaydi.
7. **Kaskaddagi manfiy qoldiq** saqlanadi va yuqoridagi tuzatishni to'smaydi. Qizil ko'rinadi va «Эътибор талаб» ro'yxatiga tushadi.
8. **Invariant.** Zanjirni boshlang'ich qoldiqdan qayta hisoblash har bir saqlangan `calc` ni aynan takrorlaydi. Test bilan tekshiriladi.

## 6. Vaqt qoidalari

1. Davr tugash vaqti (`period_end`) bilan aniqlanadi. `periods.py` da: `bounds(period_end)`, `label(period_end)` → (hisobot sanasi, 1 yoki 2), `ends_for(report_date)`, `open_for_station(now)`. Sutka qoidasi faqat `label` va `ends_for` da, shuning uchun uni o'zgartirish ma'lumotni qayta yozishni talab qilmaydi.
2. Oynalar yarim ochiq. Stansiya yoza oladi, agar `end ≤ now < end + 24 soat`. MTU va boshqarma: `end ≤ now`, yuqori chegarasiz. Tugamagan davrni hech kim kirita olmaydi.
3. Har bir forma va POST o'z `period_end` ini aniq ko'rsatadi; oyna POST paytida tekshiriladi.
4. Server vaqti stansiya sahifasida ko'rinadi va `/healthz` qaytaradi.

Misol (sutka 12.10 = 11.10 20:00 → 12.10 20:00):

| Server vaqti | Stansiya yoza oladi | O'zgarish |
|---|---|---|
| 12.10 07:59:59 | 11.10 kunduzgi, 11.10 tungi | 12.10 tungi: «08:00 да очилади» |
| 12.10 08:00:00 | 12.10 tungi, 11.10 kunduzgi | 11.10 tungi yopiladi |
| 12.10 20:00:00 | 12.10 kunduzgi, 12.10 tungi | 11.10 kunduzgi yopiladi |
| 13.10 08:00:00 | 13.10 tungi, 12.10 kunduzgi | 12.10 tungi faqat MTU/boshqarmaga qoladi |

## 7. Stansiya kirishi

- **PIN:** 8 ta tasodifiy raqam (`secrets`), `1234 5678` ko'rinishida chop etiladi, Django hasher bilan saqlanadi, ochiq holda saqlanmaydi va logga yozilmaydi.
- **Oqim:** `/` da 6 ta MTU tugmasi → stansiyalar ro'yxati → `/s/<id>` da PIN maydoni.
- **Sessiya:** `station_id` va `pin_version`, 30 kun (har so'rovda uzayadi). Har kirishda sessiya avval tozalanadi. Har so'rovda `pin_version` va `active_to` qayta tekshiriladi. Stansiya faqat sessiyadan olinadi, manzilda faqat `period_end` bo'ladi.
- **Cheklov** (hash hisoblashdan oldin): stansiya bloki; IP bo'yicha soatiga 30 ta xato (IPv6 /64 bo'yicha).
- **Blok:** har 5-ketma-ket xato stansiyaga yangi kirishni 15 daqiqa, 1 soat, 4 soat, keyin har safar 24 soatga yopadi. Muvaffaqiyatli kirish yoki PIN yangilash sanoqni nolga tushiradi. Ochiq sessiyalarga ta'sir qilmaydi.
- **MTU amallari** (`change` ga yoziladi): «Блокдан чиқариш» faqat blokni ochadi. «ПИН янгилаш» yangi PIN beradi, `pin_version + 1` qiladi, shu stansiyaning barcha sessiyalari tugaydi.
- **PIN varaq:** «ПИН яратиш» faqat hali ishlatilmagan PIN'lar uchun (`pin_hash` yoki `last_login_at` NULL). Javob sahifasi chop etiladigan varaq (stansiya, havola, PIN), `Cache-Control: no-store`. Varaq yo'qolsa amal qayta bajariladi.
- **Import:** `.xlsx`, birinchi varaq, sarlavha qatori, ustunlar `МТУ | Станция номи`; qator tartibi = saralash tartibi. Chegara: 1 MB, 1000 qator. Hammasi yoki hech narsa; xatolar qator raqami bilan. Yangi (МТУ, nom) juftlarini qo'shadi, borlarini o'tkazib yuboradi; nomni o'zgartirmaydi, o'chirmaydi, PIN'ga tegmaydi. MTU akkauntida boshqa MTU qatori xato hisoblanadi.

## 8. MTU va boshqarma kirishi

- **Akkauntlar:** `manage.py seed` `boshqarma` va 6 ta MTU foydalanuvchisini tasodifiy 16 belgili parol bilan yaratadi (bir marta chop etiladi). Parolni o'zlari almashtiradi. Boshqarma MTU parolini tiklay oladi.
- **Sessiya:** 12 soat, kirgan paytdan qat'iy.
- **Cheklov:** IP bo'yicha 15 daqiqada 10 ta xato.
- **Doira:** har bir sahifa `stations_for(user)` dan boshlanadi, begona id 404 qaytaradi. So'rovdagi MTU id faqat boshqarma uchun hisobga olinadi. Import va "stansiya qo'shish" MTU akkauntida o'z MTU sini majburlaydi.
- Har bir o'zgartiruvchi amal CSRF bilan POST.
- **Izolyatsiya testi:** har bir sahifa bo'yicha yuradi (ro'yxatga kirmagan sahifa testni yiqitadi) va A MTU sifatida javobda (HTML, Excel, grafik ma'lumoti, tarix, PIN varaq, import natijasi) B MTU stansiyasi yo'qligini, B id lari 404 berishini tekshiradi.

## 9. Ekranlar

**Stansiya (telefon uchun)**
1. **Кириш**, 7-bo'limdagidek.
2. **Асосий:** server vaqti; ochiq 2 davr uchun kartalar (yangisi tepada): oraliq, holat («Қабул қилинди 08:07» va raqamlar yoki «Топширилмаган»), muddat; keyingi davr ochilish vaqti; oxirgi 7 kun (faqat ko'rish).
3. **Киритиш:** «Давр бошида: 12», Туширилган, Кириб келган, jonli «Қолган: 9», yig'iladigan «Қолдиқни қўлда киритиш». Tugmalar: «Юбориш», «Ҳаракат бўлмади (0 / 0)». Bo'sh maydon qabul qilinmaydi.

**MTU va boshqarma** (bir xil sahifalar, boshqarmada MTU filtri)
1. **Ҳозир:** tanlangan davr (sukut bo'yicha oxirgi tugagani). Boshqarmada 6 ta «N / M» karta; MTU da «14 / 18 топширди», kiritmaganlar tepada, har birida «Киритиш». Pastda «Эътибор талаб»: oxirgi 30 kundagi `override ≠ calc` va manfiy qoldiqlar.
2. **Кунлик жадвал:** sana va shablon ko'rinishi. Kulrang = kiritilmagan, sariq = qo'lda o'zgartirilgan, qizil = manfiy, kursiv = ko'chirilgan qoldiq. Qator tuzatish formasi va tarixni ochadi.
3. **Ҳисобот:** yig'ma, grafiklar, eksport.
4. **Станциялар:** qo'shish, nomini o'zgartirish, yoqish/o'chirish, boshlang'ich qoldiq, import, PIN varaq, PIN yangilash, blokdan chiqarish; PIN holati, oxirgi kirish, blok ustunlari.
5. **Faqat boshqarma:** MTU parolini tiklash.

## 10. Hisobotlar, eksport, grafiklar

- **Qoldiq qoidasi.** T vaqtdagi qoldiq = stansiyaning `period_end ≤ T` bo'lgan oxirgi yozuvining amaldagi qoldig'i; yozuv bo'lmasa va `opening_at ≤ T` bo'lsa boshlang'ich qoldiq; aks holda bo'sh. Jadval, eksport, yig'ma va grafik bitta yordamchi funksiyadan foydalanadi. Kiritilmagan ma'lumot hech qachon nol emas.
- **Eksport.** openpyxl buyurtmachining shablon faylini to'ldiradi. MTU fayli: o'z stansiyalari va «Жами». Boshqarma fayli: MTU bo'yicha guruhlangan, oraliq jami va umumiy jami bilan. Kataklarda qiymat (formula emas), stansiya nomlari matn sifatida.
- **Eksportdagi belgilar.** Kiritilmagan kataklar bo'sh; ko'chirilgan qoldiq kulrang; bir davri yo'q sutka jami `*` bilan; qo'lda o'zgartirilgan sariq va izohda hisoblangan qiymat; manfiy qizil. Pastda: yaratilgan vaqt, akkaunt, kiritmagan stansiyalar soni.
- **Kiritmaganlar.** Bitta `period_end` uchun kutilgan stansiyalar minus yozuvlar, o'qishda hisoblanadi.
- **Yig'ma.** Hafta, oy va ixtiyoriy oraliq (366 kungacha), MTU va stansiya kesimida: tushirilgan, kirib kelgan, oraliq oxiridagi qoldiq, kiritilgan / kutilgan davrlar, qo'lda o'zgartirishlar soni.
- **Grafiklar.** Kunlik chiziqlar: tushirilgan, kirib kelgan, kun oxiridagi qoldiq. Ustunli taqqoslash: boshqarmada MTU bo'yicha, MTU da stansiya bo'yicha.

## 11. Joylashtirish va zaxira

- **Docker Compose:** `caddy` (80/443, avtomatik HTTPS), `web` (gunicorn), `db` (postgres:17). Portni faqat Caddy ochadi.
- **Sozlamalar:** cookie'lar Secure, HttpOnly, SameSite=Lax; HSTS; `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`. Mijoz IP si faqat Caddy sarlavhasidan olinadi.
- **Zaxira:** `backup.sh` kuniga 2 marta (10:00 va 22:00) `pg_dump` qiladi, `pg_restore --list` bilan tekshiradi, 30 kun saqlaydi.
- **Testlar:** `test_periods`, `test_balance`, `test_isolation`, `test_auth`.

## 12. Birinchi versiyaga kirmaydi

Shaxsiy akkauntlar va operator ismi, 2FA, SMS yoki Telegram eslatmalar va ogohlantirishlar, oflayn rejim,
REST API, Django admin, oy yopish, o'zgartirish sababi, QR kod, stansiya kodi va telefoni, yuk turi yoki
vagon raqami bo'yicha tafsilot, tashqi bulutga shifrlangan zaxira, replikatsiya.

## 13. Ishga tushirishdan oldin aniqlanadi

- Stansiyalar ro'yxati (MTU bo'yicha).
- Server (VPS), domen nomi, zaxira nusxa qayerda saqlanishi.
- Don kelmayotgan stansiyalar: har davr «Ҳаракат бўлмади» yuboradimi yoki MTU ularni vaqtincha o'chirib qo'yadimi (ikkalasi ham ishlaydi).
