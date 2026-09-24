# urls.py
from core import views
from django.urls import path
from django.views.generic import TemplateView

urlpatterns = [
    path('core/user-permissions-report/', views.user_permissions_tree, name='user_permissions_report'),
    path('core/user-permissions-tree-view/',
         views.admin_tooling('auth.view_user', 'auth.view_group')(TemplateView.as_view(
             template_name='core/user_permissions_report.html')),
         name='user_permissions_tree_view'
         ),
    path('core/compare-user-permissions/', views.compare_user_permissions, name='compare_user_permissions'),
    path('core/compare-user-permissions-data/', views.compare_user_permissions_data, name='compare_user_permissions_data'),
    path('core/search-users/', views.search_users, name='search_users'),
    path('core/toggle-group-membership/', views.toggle_group_membership, name='toggle_group_membership'),
    path('core/search-groups/', views.search_groups, name='search_groups'),
    path('core/compare-group-permissions-data/', views.compare_group_permissions_data, name='compare_group_permissions_data'),
    path('core/toggle-permission-in-group/', views.toggle_permission_in_group, name='toggle_permission_in_group'),

]
