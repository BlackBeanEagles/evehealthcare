"""Seed a realistic catalogue plus a demo patient, so the API is usable
immediately after a fresh migrate. Safe to run repeatedly.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest, TestCategory

User = get_user_model()

CENTRES = [
    {
        "name": "EVE Diagnostics Indiranagar",
        "address_line": "412, 100 Feet Road, Indiranagar",
        "city": "Bengaluru", "state": "Karnataka", "pincode": "560038",
        "phone": "+918040001111", "opens_at": "06:30", "closes_at": "21:00",
    },
    {
        "name": "EVE Diagnostics Koramangala",
        "address_line": "18, 5th Block, Koramangala",
        "city": "Bengaluru", "state": "Karnataka", "pincode": "560095",
        "phone": "+918040002222", "opens_at": "07:00", "closes_at": "20:00",
    },
    {
        "name": "EVE Diagnostics Andheri East",
        "address_line": "7, Chakala, Andheri East",
        "city": "Mumbai", "state": "Maharashtra", "pincode": "400093",
        "phone": "+912240003333", "opens_at": "07:00", "closes_at": "22:00",
    },
    {
        "name": "EVE Diagnostics Banjara Hills",
        "address_line": "Road No. 12, Banjara Hills",
        "city": "Hyderabad", "state": "Telangana", "pincode": "500034",
        "phone": "+914040004444", "opens_at": "06:00", "closes_at": "20:30",
    },
]

TESTS = [
    {
        "code": "CBC", "name": "Complete Blood Count",
        "category": TestCategory.BLOOD, "report_eta_hours": 12,
        "description": "Measures red cells, white cells, haemoglobin and platelets.",
        "preparation": "No fasting required.",
    },
    {
        "code": "LIPID", "name": "Lipid Profile",
        "category": TestCategory.BLOOD, "report_eta_hours": 24,
        "description": "Cholesterol, triglycerides, HDL and LDL.",
        "preparation": "Fast for 10-12 hours; water is allowed.",
    },
    {
        "code": "HBA1C", "name": "HbA1c (Glycated Haemoglobin)",
        "category": TestCategory.BLOOD, "report_eta_hours": 24,
        "description": "Average blood glucose over the previous three months.",
        "preparation": "No fasting required.",
    },
    {
        "code": "TSH", "name": "Thyroid Stimulating Hormone",
        "category": TestCategory.PATHOLOGY, "report_eta_hours": 24,
        "description": "Screens for hypo- and hyperthyroidism.",
        "preparation": "Preferably a morning sample.",
    },
    {
        "code": "VITD", "name": "Vitamin D (25-OH)",
        "category": TestCategory.PATHOLOGY, "report_eta_hours": 48,
        "description": "Assesses vitamin D deficiency.",
        "preparation": "No fasting required.",
    },
    {
        "code": "XRAY-CHEST", "name": "Chest X-Ray (PA view)",
        "category": TestCategory.IMAGING, "report_eta_hours": 6,
        "description": "Single posteroanterior chest radiograph.",
        "preparation": "Remove metal objects and jewellery.",
    },
    {
        "code": "ECG", "name": "Electrocardiogram",
        "category": TestCategory.CARDIOLOGY, "report_eta_hours": 2,
        "description": "12-lead resting ECG.",
        "preparation": "Avoid oily skin creams on the chest.",
    },
    {
        "code": "USG-ABD", "name": "Ultrasound Whole Abdomen",
        "category": TestCategory.IMAGING, "report_eta_hours": 8,
        "description": "Ultrasound of the abdominal and pelvic organs.",
        "preparation": "Fast for 6 hours; drink 1L water an hour before.",
    },
    {
        "code": "FULLBODY", "name": "Full Body Health Package",
        "category": TestCategory.PACKAGE, "report_eta_hours": 48,
        "description": "72 parameters covering blood, thyroid, liver and kidney.",
        "preparation": "Fast for 10-12 hours.",
    },
]

# (test code, centre index, price)
PRICES = [
    ("CBC", 0, "350.00"), ("CBC", 1, "299.00"), ("CBC", 2, "420.00"), ("CBC", 3, "310.00"),
    ("LIPID", 0, "699.00"), ("LIPID", 1, "649.00"), ("LIPID", 2, "850.00"),
    ("HBA1C", 0, "550.00"), ("HBA1C", 2, "620.00"), ("HBA1C", 3, "499.00"),
    ("TSH", 0, "400.00"), ("TSH", 1, "375.00"), ("TSH", 3, "360.00"),
    ("VITD", 0, "1200.00"), ("VITD", 2, "1450.00"),
    ("XRAY-CHEST", 0, "450.00"), ("XRAY-CHEST", 2, "500.00"),
    ("ECG", 1, "300.00"), ("ECG", 2, "350.00"), ("ECG", 3, "275.00"),
    ("USG-ABD", 2, "1800.00"), ("USG-ABD", 3, "1650.00"),
    ("FULLBODY", 0, "2999.00"), ("FULLBODY", 1, "2799.00"),
    ("FULLBODY", 2, "3499.00"), ("FULLBODY", 3, "2599.00"),
]

DEMO_USERS = [
    ("patient@example.com", "Asha Menon", "DemoPass123!", False),
    ("admin@example.com", "Ops Admin", "AdminPass123!", True),
]


class Command(BaseCommand):
    help = "Seed diagnostic centres, tests, prices and demo accounts (idempotent)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-users",
            action="store_true",
            help="Seed the catalogue only, without the demo accounts.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        centres = []
        for payload in CENTRES:
            centre, created = DiagnosticCentre.objects.update_or_create(
                name=payload["name"], city=payload["city"], defaults=payload
            )
            centres.append(centre)
            self.stdout.write(f"  {'created' if created else 'updated'} centre: {centre}")

        tests = {}
        for payload in TESTS:
            test, created = DiagnosticTest.objects.update_or_create(
                code=payload["code"], defaults=payload
            )
            tests[test.code] = test
            self.stdout.write(f"  {'created' if created else 'updated'} test:   {test}")

        for code, centre_index, price in PRICES:
            CentreTest.objects.update_or_create(
                centre=centres[centre_index],
                test=tests[code],
                defaults={"price": Decimal(price), "is_available": True},
            )
        self.stdout.write(f"  priced {len(PRICES)} offerings")

        if not options["no_users"]:
            for email, full_name, password, is_staff in DEMO_USERS:
                if User.objects.filter(email=email).exists():
                    self.stdout.write(f"  demo user exists: {email}")
                    continue
                factory = User.objects.create_superuser if is_staff else User.objects.create_user
                factory(email=email, full_name=full_name, password=password)
                self.stdout.write(f"  created demo user: {email} / {password}")

        self.stdout.write(self.style.SUCCESS("Catalogue seeded."))
