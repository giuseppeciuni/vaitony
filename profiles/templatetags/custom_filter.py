from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Accedi a un elemento di un dizionario usando una chiave nel template Django.
    Utilizzo: {{ dictionary|get_item:key }}
    """
    return dictionary.get(key, [])



@register.filter
def multiply(value, arg):
    """Moltiplica il valore per l'argomento"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return value