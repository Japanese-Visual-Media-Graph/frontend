from django.urls import path

from . import views

urlpatterns = [
    path('applications/search', views.search, name='search'),
    path('applications/get_search_page', views.get_search_page, name='get_search_page'),
    path('applications/crosstab', views.uri_crosstab, name="uri_crosstab"),
    path('overview', views.overview, name="overview"),
    path('jvmg/<path:path>', views.get_cluster, name="get_cluster"),
    path('<path:path>', views.main, name="main"),
   ]
