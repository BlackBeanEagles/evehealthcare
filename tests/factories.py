"""Model factories for the test suite."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest, TestCategory
from apps.payments.models import Payment, PaymentStatus

User = get_user_model()

DEFAULT_PASSWORD = "TestPass123!"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"patient{n}@example.com")
    full_name = factory.Sequence(lambda n: f"Patient {n}")
    phone = "+919876543210"

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        obj.set_password(extracted or DEFAULT_PASSWORD)
        if create:
            obj.save(update_fields=["password"])


class StaffUserFactory(UserFactory):
    email = factory.Sequence(lambda n: f"staff{n}@example.com")
    full_name = "Ops Staff"
    is_staff = True


class DiagnosticCentreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DiagnosticCentre

    name = factory.Sequence(lambda n: f"EVE Centre {n}")
    address_line = "12 Test Road"
    city = "Bengaluru"
    state = "Karnataka"
    pincode = "560001"
    phone = "+918040000000"
    is_active = True


class DiagnosticTestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DiagnosticTest

    code = factory.Sequence(lambda n: f"TST{n}")
    name = factory.Sequence(lambda n: f"Diagnostic Test {n}")
    description = "A test."
    category = TestCategory.BLOOD
    is_active = True


class CentreTestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CentreTest

    centre = factory.SubFactory(DiagnosticCentreFactory)
    test = factory.SubFactory(DiagnosticTestFactory)
    price = Decimal("500.00")
    is_available = True


class BookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Booking

    user = factory.SubFactory(UserFactory)
    centre_test = factory.SubFactory(CentreTestFactory)
    # A distinct slot per booking: a patient may not hold two active bookings
    # for the same offering at the same time, so a constant default would
    # collide whenever a test builds two of them.
    @factory.lazy_attribute_sequence
    def scheduled_for(obj, n):
        day = timezone.localtime(timezone.now()) + timedelta(days=1 + (n // 12) % 60)
        return day.replace(hour=8 + (n % 12), minute=0, second=0, microsecond=0)

    amount = factory.LazyAttribute(lambda obj: obj.centre_test.price)
    currency = "INR"
    status = BookingStatus.PENDING


class PaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Payment

    booking = factory.SubFactory(BookingFactory)
    amount = factory.LazyAttribute(lambda obj: obj.booking.amount)
    currency = "INR"
    status = PaymentStatus.PENDING
    method = "UPI"
