from rest_framework.authentication import SessionAuthentication

class ApiSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        return "Session"
