from django.urls import path
from . import views

urlpatterns = [
    path("", views.login_view, name="login"),
    path("exam/", views.exam_view, name="exam"),
    path("exam/answer/", views.submit_answer, name="submit_answer"),
    path("review/", views.review_view, name="review"),
    path("review/answer/", views.submit_review_answer, name="submit_review_answer"),
    path("finish/", views.finalize_submission, name="finalize_submission"),
    path("locked/", views.locked_view, name="locked"),
    path("tab-violation/", views.tab_violation, name="tab_violation"),
    path("report-violation/", views.report_violation, name="report_violation"),
    path("status/", views.status_api, name="status_api"),
    path("teacher/monitor/<int:exam_id>/", views.teacher_monitor, name="teacher_monitor"),
    path("teacher/monitor/<int:exam_id>/data/", views.teacher_monitor_data, name="teacher_monitor_data"),
]
