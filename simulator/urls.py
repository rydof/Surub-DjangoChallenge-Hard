from django.urls import path

from simulator import views

urlpatterns = [
    path('', views.home, name='home'),
    path('unit', views.unit_create, name='unit_create'),
    path('unit/', views.unit_create),
    path('unit/all', views.unit_list, name='unit_list'),
    path('unit/all/', views.unit_list),
    path('unit/<int:unit_id>', views.unit_detail, name='unit_detail'),
    path('unit/<int:unit_id>/', views.unit_detail),
    path('unit/<int:unit_id>/heal', views.unit_heal, name='unit_heal'),
    path('unit/<int:unit_id>/heal/', views.unit_heal),
    path('people', views.people_list, name='people_list'),
    path('people/', views.people_list),
    path('people/download', views.people_download, name='people_download'),
    path('people/download/', views.people_download),
    path('people/draft', views.people_draft, name='people_draft'),
    path('people/draft/', views.people_draft),
    path('people/follow-key', views.people_follow_key, name='people_follow_key'),
    path('people/follow-key/', views.people_follow_key),
    path('people/reset', views.people_reset, name='people_reset'),
    path('people/reset/', views.people_reset),
    path('simulate', views.simulate_page, name='simulate_page'),
    path('simulate/', views.simulate_page),
    path('simulate/fetch', views.simulate_fetch, name='simulate_fetch'),
    path('simulate/fetch/', views.simulate_fetch),
    path('simulate/reset', views.simulate_reset, name='simulate_reset'),
    path('simulate/reset/', views.simulate_reset),
    path('simulate/clear', views.simulate_clear, name='simulate_clear'),
    path('simulate/clear/', views.simulate_clear),
    path('tasks/status', views.tasks_status, name='tasks_status'),
]
