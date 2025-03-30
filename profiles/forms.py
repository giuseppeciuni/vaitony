from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm




class UserRegisterForm(UserCreationForm):
    email = forms.EmailField()
    username = forms.CharField(widget=forms.TextInput( attrs={ 'class': 'form-control',}) )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']




class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(required=True)
