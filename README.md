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

Kerak: Linux server, Docker va Compose plagini, domen (A yozuvi server IP siga), ochiq 80 va 443 portlar.
Faqat Caddy port ochadi; HTTPS sertifikatini o'zi oladi va yangilaydi.

```sh
git clone <repo> /opt/don-hisobot && cd /opt/don-hisobot
cp .env.example .env            # DOMAIN, SECRET_KEY, DB_PASSWORD ni to'ldiring
docker compose up -d --build    # migratsiyalar web ishga tushganda bajariladi
docker compose exec web python manage.py seed
curl https://<domen>/healthz    # {"ok": true, "time": "..."}
```

`SECRET_KEY` yaratish: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.

Yangilash: `git pull && docker compose up -d --build`.
Loglar: `docker compose logs -f web`.

## Zaxira

`backup.sh` bazani `backups/hisobot-YYYY-MM-DD_HHMM.dump` ga yozadi, `pg_restore --list` bilan tekshiradi,
30 kundan eskilarini o'chiradi. Xato bo'lsa nol bo'lmagan kod bilan chiqadi.

Server vaqt zonasini Toshkentga qo'ying va cron ga kuniga ikki marta qo'shing (`crontab -e`):

```sh
sudo timedatectl set-timezone Asia/Tashkent
```

```
0 10,22 * * * /opt/don-hisobot/backup.sh >> /var/log/hisobot-backup.log 2>&1
```

`backups/` papkasini muntazam ravishda serverdan tashqariga ko'chiring: server yo'qolsa, zaxira ham yo'qoladi.

### Tiklash

```sh
docker compose stop web
docker compose exec -T db pg_restore -U hisobot -d hisobot --clean --if-exists < backups/hisobot-2026-10-12_2200.dump
docker compose start web
```

Sessiyalar zaxiraga kirmaydi: tiklangandan keyin hamma qayta kiradi.

## Parolni tiklash

- Boshqarma paroli: `docker compose exec web python manage.py changepassword boshqarma`
  (mahalliy: `uv run python manage.py changepassword boshqarma`).
- MTU paroli: boshqarma akkauntida «МТУ пароллари» sahifasi. Yangi parol bir marta ko'rsatiladi.
