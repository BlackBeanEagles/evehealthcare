"""Catalogue endpoints.

Reads are open to any authenticated user and cached; writes are staff-only.
Filtering is implemented explicitly rather than via django-filter: there are
five filters in total and hand-rolling them keeps the dependency list short
and the generated OpenAPI parameters accurate.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Count, Prefetch, Q, QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.catalog.cache import CachedListMixin
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.catalog.serializers import (
    CentreTestSerializer,
    CentreTestWriteSerializer,
    DiagnosticCentreDetailSerializer,
    DiagnosticCentreSerializer,
    DiagnosticTestSerializer,
    OfferingSerializer,
)
from apps.common.exceptions import PermissionDenied, ValidationFailed
from apps.common.permissions import IsAdminOrReadOnly

INCLUDE_INACTIVE = OpenApiParameter(
    "include_inactive",
    bool,
    description="Staff only. Include rows that have been deactivated.",
)


def wants_inactive(request: Request) -> bool:
    """Resolve ``?include_inactive=`` and enforce that it is staff-only.

    It is rejected rather than silently ignored for non-staff so that a cached
    response can never be served to the wrong audience: the flag is part of
    the cache key, and only staff can ever produce a request carrying it.
    """
    raw = request.query_params.get("include_inactive", "").lower()
    if raw not in {"1", "true", "yes"}:
        return False
    if not (request.user and request.user.is_staff):
        raise PermissionDenied("include_inactive is available to staff users only.")
    return True


def parse_decimal(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValidationFailed(f"{field} must be a number.", details={field: raw})


@extend_schema_view(
    list=extend_schema(
        tags=["catalogue"],
        summary="List diagnostic tests",
        parameters=[
            OpenApiParameter("search", str, description="Match name, code or description."),
            OpenApiParameter("category", str, description="BLOOD, IMAGING, CARDIOLOGY, ..."),
            INCLUDE_INACTIVE,
        ],
    ),
    retrieve=extend_schema(tags=["catalogue"], summary="Retrieve a diagnostic test"),
    create=extend_schema(tags=["catalogue"], summary="Create a test (staff)"),
    update=extend_schema(tags=["catalogue"], summary="Replace a test (staff)"),
    partial_update=extend_schema(tags=["catalogue"], summary="Update a test (staff)"),
    destroy=extend_schema(tags=["catalogue"], summary="Delete a test (staff)"),
)
class DiagnosticTestViewSet(CachedListMixin, viewsets.ModelViewSet):
    serializer_class = DiagnosticTestSerializer
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]

    def get_queryset(self) -> QuerySet[DiagnosticTest]:
        queryset = DiagnosticTest.objects.all()
        if not wants_inactive(self.request):
            queryset = queryset.filter(is_active=True)

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(description__icontains=search)
            )
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category.upper())
        return queryset.order_by("name", "id")


@extend_schema_view(
    list=extend_schema(
        tags=["catalogue"],
        summary="List diagnostic centres",
        parameters=[
            OpenApiParameter("city", str, description="Exact city match (case-insensitive)."),
            OpenApiParameter("search", str, description="Match centre name or address."),
            OpenApiParameter("test", str, description="Only centres offering this test code or id."),
            INCLUDE_INACTIVE,
        ],
    ),
    retrieve=extend_schema(
        tags=["catalogue"],
        summary="Retrieve a centre with its price list",
    ),
    create=extend_schema(tags=["catalogue"], summary="Create a centre (staff)"),
    update=extend_schema(tags=["catalogue"], summary="Replace a centre (staff)"),
    partial_update=extend_schema(tags=["catalogue"], summary="Update a centre (staff)"),
    destroy=extend_schema(tags=["catalogue"], summary="Delete a centre (staff)"),
)
class DiagnosticCentreViewSet(CachedListMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return DiagnosticCentreDetailSerializer
        if self.action == "tests":
            return OfferingSerializer
        return DiagnosticCentreSerializer

    def get_queryset(self) -> QuerySet[DiagnosticCentre]:
        queryset = DiagnosticCentre.objects.annotate(
            tests_offered=Count("offerings", filter=Q(offerings__is_available=True))
        )
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "offerings",
                    queryset=CentreTest.objects.select_related("test", "centre"),
                )
            )
        if not wants_inactive(self.request):
            queryset = queryset.filter(is_active=True)

        city = self.request.query_params.get("city")
        if city:
            queryset = queryset.filter(city__iexact=city.strip())

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(address_line__icontains=search)
            )

        test = self.request.query_params.get("test")
        if test:
            match = Q(offerings__test__code__iexact=test)
            if test.isdigit():
                match |= Q(offerings__test_id=int(test))
            queryset = queryset.filter(match, offerings__is_available=True).distinct()
        return queryset.order_by("name", "id")

    @extend_schema(
        tags=["catalogue"],
        summary="Tests offered by this centre, with prices",
        responses={200: OfferingSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="tests")
    def tests(self, request: Request, pk: str | None = None) -> Response:
        centre = self.get_object()
        offerings = (
            centre.offerings.select_related("test", "centre")
            .filter(is_available=True, test__is_active=True)
            .order_by("test__name")
        )
        page = self.paginate_queryset(offerings)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=["catalogue"],
        summary="List offerings (a test at a centre, with its price)",
        description=(
            "The bookable unit. Use this to answer 'where can I get test X, "
            "and what does it cost?' - filter by test and sort by price."
        ),
        parameters=[
            OpenApiParameter("test", str, description="Test code or id."),
            OpenApiParameter("centre", int, description="Centre id."),
            OpenApiParameter("city", str, description="Centre city."),
            OpenApiParameter("max_price", str, description="Upper price bound."),
            OpenApiParameter("ordering", str, description="price or -price (default: price)."),
            INCLUDE_INACTIVE,
        ],
    ),
    retrieve=extend_schema(tags=["catalogue"], summary="Retrieve an offering"),
    create=extend_schema(tags=["catalogue"], summary="Add a test to a centre (staff)"),
    update=extend_schema(tags=["catalogue"], summary="Replace an offering (staff)"),
    partial_update=extend_schema(tags=["catalogue"], summary="Reprice an offering (staff)"),
    destroy=extend_schema(tags=["catalogue"], summary="Remove an offering (staff)"),
)
class CentreTestViewSet(CachedListMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrReadOnly]

    def get_serializer_class(self):
        if self.request.method in {"POST", "PUT", "PATCH"}:
            return CentreTestWriteSerializer
        return CentreTestSerializer

    def get_queryset(self) -> QuerySet[CentreTest]:
        queryset = CentreTest.objects.select_related("centre", "test")
        if not wants_inactive(self.request):
            queryset = queryset.filter(
                is_available=True, centre__is_active=True, test__is_active=True
            )

        test = self.request.query_params.get("test")
        if test:
            queryset = (
                queryset.filter(test_id=int(test))
                if test.isdigit()
                else queryset.filter(test__code__iexact=test)
            )
        centre = self.request.query_params.get("centre")
        if centre:
            if not centre.isdigit():
                raise ValidationFailed("centre must be a numeric id.")
            queryset = queryset.filter(centre_id=int(centre))
        city = self.request.query_params.get("city")
        if city:
            queryset = queryset.filter(centre__city__iexact=city.strip())
        max_price = self.request.query_params.get("max_price")
        if max_price:
            queryset = queryset.filter(price__lte=parse_decimal(max_price, "max_price"))

        ordering = self.request.query_params.get("ordering", "price")
        if ordering not in {"price", "-price"}:
            raise ValidationFailed("ordering must be 'price' or '-price'.")
        return queryset.order_by(ordering, "id")
