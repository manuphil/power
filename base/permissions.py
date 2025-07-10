from rest_framework import permissions

class IsOwnerOrReadOnly(permissions.BasePermission):
    """Permission pour les propriétaires ou lecture seule"""
    
    def has_object_permission(self, request, view, obj):
        # Lecture pour tous les utilisateurs authentifiés
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Écriture seulement pour le propriétaire
        return obj == request.user

class IsAdminOrReadOnly(permissions.BasePermission):
    """Permission admin ou lecture seule"""
    
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_staff
