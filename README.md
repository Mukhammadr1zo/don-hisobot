# Don hisoboti

Don mahsulotlarini kunlik tushirish hisoboti (O'zbekiston temir yo'llari). Stansiyalar ma'lumotni PIN orqali
kiritadi, 6 ta MTU o'z stansiyalarini, Yuk va tijorat boshqarmasi hammasini ko'radi.
Django 5.2, PostgreSQL 17, interfeys o'zbek kirill yozuvida.

Dizayn: `docs/superpowers/specs/2026-10-12-don-hisobot-design.md`.

## Mahalliy ishga tushirish

Kerak: [uv](https://docs.astral.sh/uv/) va PostgreSQL.

```sh
docker compose -f compose.dev.yaml up -d        # PostgreSQL 127.0.0.1:5440 da
uv run python manage.py migrate
uv run python manage.py seed                    # 6 MTU va 7 akkaunt; parollar bir marta chiqadi
uv run python manage.py runserver               # http://127.0.0.1:8000/
```

Docker bo'lmasa, oddiy PostgreSQL ham bo'ladi: `hisobot` foydalanuvchisi (paroli `hisobot`, `CREATEDB` huquqi bilan)
va `hisobot` bazasini yarating, so'ng portni ko'rsating:

```sh
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=hisobot DB_USER=hisobot DB_PASSWORD=hisobot
```

Testlar: `uv run python manage.py test hisobot --noinput`.
`DEBUG=1` da `/_uslub/` sahifasi interfeysning barcha qismlarini ko'rsatadi.

## Birinchi sozlash

1. `seed` chiqargan parollarni saqlang: `boshqarma` va MTU akkauntlari (`toshkent`, `qoqon`, `buxoro`,
   `qongirot`, `qarshi`, `termiz`). Har kim birinchi kirishda «Парол» sahifasida parolini almashtiradi.
2. MTU akkaunti bilan `/kirish/` orqali kiring.
3. «Станциялар» → «Импорт»: birinchi varag'ida `МТУ | Станция номи` sarlavhali `.xlsx` faylni yuklang
   (1 MB va 1000 qatorgacha). Qatorlar tartibi ro'yxat tartibi bo'ladi. Qayta import faqat yangi stansiyalarni qo'shadi.
4. «Станциялар» → «ПИН яратиш»: hali ishlatilmagan PIN'lar uchun chop etiladigan varaq ochiladi.
   PIN faqat shu varaqda bir marta ko'rinadi. Varaqni chop etib, kesib, stansiyalarga bering.
5. Stansiya havolani ochadi (yoki bosh sahifada MTU → stansiya), PIN ni kiritadi. Birinchi yozuvda
   «Давр бошидаги қолдиқ» ham so'raladi.

PIN yo'qolsa: «Станциялар» → stansiya → «ПИН янгилаш» (eski PIN va ochiq sessiyalar bekor bo'ladi).
Ko'p xato kiritilgani uchun bloklangan stansiya: «Блокдан чиқариш».

## Serverga joylashtirish

Kerak: Linux server (masalan Ubuntu 24.04, 2 GB RAM), Docker Engine va Compose plagini
(https://docs.docker.com/engine/install/), domen (A yozuvi server IP siga), ochiq 80 va 443 portlar.
Faqat Caddy port ochadi; HTTPS sertifikatini o'zi oladi va yangilaydi.
Quyidagi buyruqlar root sifatida bajariladi (`sudo -i`).

Server soati to'g'ri bo'lishi shart: davrlar 08:00 va 20:00 da ochiladi va stansiya oynasi 24 soat.
`timedatectl` da `System clock synchronized: yes` bo'lsin (yo'q bo'lsa: `timedatectl set-ntp true`).
Firewall ishlatilsa, faqat 22, 80 va 443 ochiq bo'lsin (`ufw allow OpenSSH; ufw allow 80,443/tcp; ufw enable`).
80 va 443 portlarni boshqa dastur egallamagan bo'lsin: `ss -Hltun '( sport = :80 or sport = :443 )'` hech narsa
chiqarmasligi kerak. Chiqarsa (odatda apache2 yoki nginx), o'sha xizmatni butunlay o'chiring, aks holda u qayta
yuklashda portni yana egallaydi: `systemctl disable --now apache2` (yoki `nginx`).

```sh
git clone <repo> /opt/don-hisobot && cd /opt/don-hisobot
cp .env.example .env && chmod 600 .env   # DOMAIN, SECRET_KEY, DB_PASSWORD ni to'ldiring
docker compose up -d --build --wait      # migratsiyalar tugab, web ishga tushguncha kutadi
docker compose exec web python manage.py seed
curl https://<domen>/healthz             # {"ok": true, "time": "..."}
```

`SECRET_KEY` va `DB_PASSWORD` yaratish: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
`DB_PASSWORD` bazaga faqat birinchi `docker compose up` da yoziladi (`pgdata` tomi bo'sh bo'lganda); keyin `.env` da
o'zgartirish bazani o'zgartirmaydi va web ulana olmaydi (`password authentication failed`). Almashtirish kerak
bo'lsa, avval bazada: `docker compose exec db psql -U hisobot -c "ALTER USER hisobot PASSWORD 'yangi'"`, so'ng
`.env` ni o'zgartirib `docker compose up -d --wait`. `docker compose down -v` ishlatmang: u bazani o'chiradi.

`up` «address already in use» bilan to'xtagan bo'lsa: portni bo'shatib, `docker compose up -d --wait
--force-recreate caddy` bajaring (oddiy qayta `up` Caddy ni portsiz ishga tushirishi mumkin).

Yangilash (avval zaxira; Caddy `Caddyfile` o'zgarishini faqat qayta ishga tushganda ko'radi):

```sh
./backup.sh && git pull && docker compose up -d --build --wait && docker compose restart caddy
```

Loglar: `docker compose logs -f web` (ilova xatolari traceback bilan shu yerda). HTTPS/sertifikat muammosi
(`curl` TLS xatosi bersa) `docker compose logs caddy` da ko'rinadi: domen A yozuvi server IP siga qaraganini va
80/443 portlar tashqaridan ochiqligini tekshiring.

Ishga tushgandan keyin tekshiring:
- `docker compose ps`: `db` va `web` `healthy`; `caddy` da healthcheck yo'q, u `Up` va PORTS ustunida
  `0.0.0.0:80` va `0.0.0.0:443` ko'rinadi. Haqiqiy tekshiruv: `curl https://<domen>/healthz` → `"ok": true`.
- Telefondan noto'g'ri PIN kiriting, so'ng
  `docker compose exec db psql -U hisobot -tAc "select ip from hisobot_loginfailure order by at desc limit 1"`
  telefonning haqiqiy IP sini ko'rsatsin. Har doim `172.x.x.1` chiqsa, mijozlar IPv6 orqali Docker proksisidan
  kelyapti va hamma bitta IP bo'lib ko'rinadi (bitta odamning xatolari hammani cheklaydi): serverda IPv6 ni
  Docker uchun yoqing yoki domenga faqat A (IPv4) yozuvini qoldiring.

## Zaxira

`backup.sh` bazani `backups/hisobot-YYYY-MM-DD_HHMM.dump` ga yozadi, `pg_restore --list` bilan tekshiradi,
30 kundan eskilarini o'chiradi. Xato bo'lsa nol bo'lmagan kod bilan chiqadi.

Server vaqt zonasini Toshkentga qo'ying (cron yangi zonani qayta ishga tushgandan keyin oladi):

```sh
timedatectl set-timezone Asia/Tashkent
systemctl restart cron
```

root ning cron jadvaliga (`crontab -e`, root sifatida) kuniga ikki marta qo'shing:

```
0 10,22 * * * /opt/don-hisobot/backup.sh >> /var/log/hisobot-backup.log 2>&1
```

Bir marta qo'lda ishga tushirib tekshiring: `/opt/don-hisobot/backup.sh` yangi `.dump` fayl nomini chiqarishi kerak.
Zaxira fayllari faqat egasiga o'qiladi (ularda PIN va parol xeshlari bor).

`backups/` papkasini muntazam ravishda serverdan tashqariga ko'chiring: server yo'qolsa, zaxira ham yo'qoladi.

### Tiklash

Baza yangidan yaratiladi va zaxira bitta tranzaksiyada to'liq yoziladi; keyin `migrate` sxemani joriy kodga
olib chiqadi. Birinchi qator hozirgi holatni ham saqlaydi: `dropdb` uni o'chiradi.

```sh
./backup.sh
docker compose stop web
docker compose exec -T db dropdb -U hisobot --maintenance-db=postgres hisobot
docker compose exec -T db createdb -U hisobot hisobot
docker compose exec -T db pg_restore -U hisobot -d hisobot --single-transaction --exit-on-error < backups/hisobot-2026-10-12_2200.dump
docker compose up -d --wait
```

`pg_restore` xato chiqarsa, web ni ishga tushirmang: baza bo'sh qoladi. Boshqa `.dump` bilan qayta urinib ko'ring
yoki birinchi qatorda olingan zaxirani tiklang.

Sessiyalar zaxiraga kirmaydi: tiklangandan keyin hamma qayta kiradi. Zaxiradan keyin yangilangan PIN va
parollar ham eski holiga qaytadi: kerak bo'lsa ularni qayta yangilang.

## Parolni tiklash

- Boshqarma paroli: `docker compose exec web python manage.py changepassword boshqarma`
  (mahalliy: `uv run python manage.py changepassword boshqarma`). Parol kamida 10 belgi va oddiy bo'lmasligi
  kerak; o'zgartirgandan keyin foydalanuvchi uni «Парол» sahifasida o'zinikiga almashtiradi.
- MTU paroli: boshqarma akkauntida «МТУ пароллари» sahifasi. Yangi parol bir marta ko'rsatiladi.
