from django.urls import path
from identity.api import CsrfView, LoginView, LogoutView, SessionView
from proofing.api import OrderDetailView, OrderListView, OrderCommandView, DecisionView, CustomerRequestView

from files.api import UploadView, ValidateView, ContentView

from identity.invitations import IssueInvitationView, ChallengeView, VerifyView, RevokeAccessView

from exports.api import ExportView, ExportDetailView

urlpatterns = [
    path("api/exports", ExportView.as_view()),
    path("api/exports/<uuid:job_id>", ExportDetailView.as_view()),
    path("api/orders/<uuid:order_id>/commands/<str:action>", OrderCommandView.as_view()),
    path("api/requests/<uuid:request_id>/decision", DecisionView.as_view()),
    path("api/requests/<uuid:request_id>", CustomerRequestView.as_view()),
    path("api/access/<uuid:owner_id>/invitations", IssueInvitationView.as_view()),
    path("api/access/<uuid:owner_id>/revoke", RevokeAccessView.as_view()),
    path("api/access/challenge", ChallengeView.as_view()),
    path("api/access/verify", VerifyView.as_view()),
    path("api/files", UploadView.as_view()),
    path("api/files/<uuid:file_id>/validate", ValidateView.as_view()),
    path("api/files/<uuid:file_id>/content", ContentView.as_view()),
    path("api/session", SessionView.as_view()),
    path("api/session/csrf", CsrfView.as_view()),
    path("api/session/login", LoginView.as_view()),
    path("api/session/logout", LogoutView.as_view()),
    path("api/orders", OrderListView.as_view()),
    path("api/orders/<uuid:order_id>", OrderDetailView.as_view()),
]
