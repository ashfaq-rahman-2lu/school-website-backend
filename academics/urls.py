from django.urls import path

from . import views

urlpatterns = [
    path("public/home/", views.public_home, name="public-home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("students/", views.student_list, name="students"),
    path("academic-setup/", views.academic_setup, name="academic-setup"),
    path("exams/", views.exam_list, name="exams"),
    path("exams/<int:exam_id>/results/", views.result_sheet, name="result-sheet"),
    path("exams/<int:exam_id>/lock/", views.exam_lock, name="exam-lock"),
    path("exams/<int:exam_id>/publish/", views.exam_publish, name="exam-publish"),
    path("exams/<int:exam_id>/open/", views.exam_open, name="exam-open"),
    path("staff/", views.staff_list, name="staff-list"),
    path("staff/<int:membership_id>/", views.staff_role, name="staff-role"),
    path("exam-subjects/<int:exam_subject_id>/mark/", views.enter_mark, name="enter-mark"),
    path("marks/<int:mark_id>/correct/", views.mark_correction, name="mark-correction"),
    path("portal/students/", views.portal_students, name="portal-students"),
    path("portal/students/<int:student_id>/", views.portal_student_detail, name="portal-student-detail"),
]
