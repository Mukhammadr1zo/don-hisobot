from django.conf import settings
from django.urls import path

from . import views_public, views_reports, views_staff, views_station, views_stations

urlpatterns = [
    path("", views_public.home, name="home"),
    path("m/<int:mtu_id>/", views_public.mtu_stations, name="mtu_stations"),
    path("s/<int:station_id>/", views_public.station_login, name="station_login"),
    path("kirish/", views_public.staff_login, name="staff_login"),
    path("chiqish/", views_public.logout, name="logout"),
    path("healthz", views_public.healthz, name="healthz"),
    path("stansiya/", views_station.home, name="station_home"),
    path("stansiya/davr/<str:pe>/", views_station.entry, name="station_entry"),
    path("xodim/", views_staff.now, name="staff_now"),
    path("xodim/jadval/", views_staff.daily, name="staff_daily"),
    path("xodim/yozuv/<int:station_id>/<str:pe>/", views_staff.entry, name="staff_entry"),
    path("xodim/hisobot/", views_reports.report, name="staff_report"),
    path("xodim/eksport/", views_reports.export, name="staff_export"),
    path("xodim/stansiyalar/", views_stations.index, name="stations"),
    path("xodim/stansiyalar/<int:station_id>/", views_stations.edit, name="station_edit"),
    path("xodim/stansiyalar/<int:station_id>/holat/", views_stations.toggle, name="station_toggle"),
    path("xodim/stansiyalar/<int:station_id>/pin/", views_stations.regenerate_pin, name="station_pin"),
    path("xodim/stansiyalar/<int:station_id>/blok/", views_stations.unlock, name="station_unlock"),
    path("xodim/stansiyalar/import/", views_stations.import_roster, name="stations_import"),
    path("xodim/stansiyalar/pin-varaq/", views_stations.pin_sheet, name="pin_sheet"),
    path("xodim/parol/", views_stations.password_change, name="password_change"),
    path("xodim/mtu-parol/", views_stations.mtu_passwords, name="mtu_passwords"),
]

if settings.DEBUG:
    urlpatterns.append(path("_uslub/", views_public.styleguide, name="styleguide"))
