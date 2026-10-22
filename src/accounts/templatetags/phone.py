from django import template

from accounts import passwords, phone

register = template.Library()

register.filter("phone_br", phone.format_phone)
register.filter("whatsapp_url", phone.whatsapp_url)
register.simple_tag(passwords.credentials_message, name="credentials_message")
