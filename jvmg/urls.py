from django.urls import path

from . import views

urlpatterns = [
    path('applications/search', views.search, name='search'),
    path('applications/crosstab', views.uri_crosstab, name="uri_crosstab"),
    path('<path:path>/ont/', views.uri_lookup_ont, name="uri_lookup_ont"),
    path('<path:path>', views.main, name="main"),
   ]
