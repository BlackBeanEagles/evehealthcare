from rest_framework.routers import DefaultRouter

from apps.bookings.views import BookingViewSet

app_name = "bookings"

router = DefaultRouter()
router.register("bookings", BookingViewSet, basename="booking")

urlpatterns = router.urls
