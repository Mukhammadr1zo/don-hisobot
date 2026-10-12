import secrets

from django.core.management.base import BaseCommand
from django.db import transaction

from hisobot.models import Mtu, User

MTUS = [
    ("Тошкент", "toshkent"),
    ("Қўқон", "qoqon"),
    ("Бухоро", "buxoro"),
    ("Қўнғирот", "qongirot"),
    ("Қарши", "qarshi"),
    ("Термиз", "termiz"),
]


class Command(BaseCommand):
    help = "6 ta MTU va 7 ta foydalanuvchini yaratadi. Yangi foydalanuvchi paroli bir marta chop etiladi."

    @transaction.atomic
    def handle(self, *args, **options):
        accounts = [("boshqarma", None)]
        for sort, (name, username) in enumerate(MTUS, start=1):
            mtu, _ = Mtu.objects.get_or_create(name=name, defaults={"sort": sort})
            accounts.append((username, mtu))
        for username, mtu in accounts:
            if User.objects.filter(username=username).exists():
                continue
            password = secrets.token_urlsafe(12)  # 12 random bytes are exactly 16 characters
            User.objects.create_user(username, password=password, mtu=mtu)
            self.stdout.write(f"{username}: {password}")
