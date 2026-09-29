"""The diagnostic catalogue: centres, tests, and what each centre charges.

The important modelling decision here is that **price lives on the
centre/test relationship, not on the test**. The same lipid profile is 450 at
one centre and 799 at another, so a ``price`` column on ``DiagnosticTest``
would be wrong the moment a second centre is onboarded.
"""
from __future__ import annotations

from datetime import time
from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from apps.common.models import TimeStampedModel

pincode_validator = RegexValidator(
    regex=r"^\d{4,10}$", message="Enter a valid postal code."
)


class TestCategory(models.TextChoices):
    BLOOD = "BLOOD", "Blood test"
    IMAGING = "IMAGING", "Imaging"
    CARDIOLOGY = "CARDIOLOGY", "Cardiology"
    PATHOLOGY = "PATHOLOGY", "Pathology"
    PACKAGE = "PACKAGE", "Health package"
    OTHER = "OTHER", "Other"


class DiagnosticCentre(TimeStampedModel):
    """A physical lab or collection centre."""

    name = models.CharField(max_length=150)
    address_line = models.CharField(max_length=255)
    city = models.CharField(max_length=80, db_index=True)
    state = models.CharField(max_length=80)
    pincode = models.CharField(max_length=10, validators=[pincode_validator])
    phone = models.CharField(max_length=16, blank=True)
    opens_at = models.TimeField(default=time(7, 0))
    closes_at = models.TimeField(default=time(21, 0))
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "diagnostic_centres"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "city"], name="uniq_centre_name_per_city"
            ),
        ]
        indexes = [models.Index(fields=["city", "is_active"], name="idx_centre_city_active")]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}"

    @property
    def location(self) -> str:
        return f"{self.address_line}, {self.city}, {self.state} {self.pincode}"


class DiagnosticTest(TimeStampedModel):
    """A test in the master catalogue, independent of who performs it."""

    code = models.CharField(
        max_length=32,
        unique=True,
        help_text="Stable short code, e.g. CBC or LIPID-PROFILE.",
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20, choices=TestCategory.choices, default=TestCategory.OTHER,
        db_index=True,
    )
    preparation = models.TextField(
        blank=True, help_text="Patient instructions, e.g. 10 hours of fasting."
    )
    report_eta_hours = models.PositiveSmallIntegerField(default=24)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "diagnostic_tests"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class CentreTest(TimeStampedModel):
    """A test offered by a centre at a price. This is what a patient books."""

    centre = models.ForeignKey(
        DiagnosticCentre, on_delete=models.CASCADE, related_name="offerings"
    )
    test = models.ForeignKey(
        DiagnosticTest, on_delete=models.CASCADE, related_name="offerings"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Current list price. Bookings snapshot this value.",
    )
    is_available = models.BooleanField(
        default=True,
        help_text="Temporarily stop taking bookings without deleting the offering.",
    )

    class Meta:
        db_table = "centre_tests"
        ordering = ["price"]
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "test"], name="uniq_test_per_centre"
            ),
            models.CheckConstraint(
                check=models.Q(price__gt=Decimal("0")), name="centre_test_price_positive"
            ),
        ]
        indexes = [
            models.Index(fields=["test", "price"], name="idx_offering_test_price"),
        ]

    def __str__(self) -> str:
        return f"{self.test.code} @ {self.centre.name} = {self.price}"

    @property
    def is_bookable(self) -> bool:
        """An offering can only be booked if every layer above it is live."""
        return self.is_available and self.centre.is_active and self.test.is_active
