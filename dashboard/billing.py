from datetime import datetime, timedelta

from profiles.models import UserSubscription, InvoiceItem


def reset_monthly_usage():
    """
    Resetta il conteggio mensile delle query RAG all'inizio di ogni mese.
    """
    today = datetime.now().date()
    UserSubscription.objects.filter(is_active=True).update(
        current_month_rag_queries=0,
        last_usage_reset=today
    )

def generate_monthly_invoices():
    """
    Genera le fatture mensili per tutti gli utenti attivi.
    """
    today = datetime.now().date()
    start_of_month = today.replace(day=1)

    if today.day != 1:
        # Esegui solo il primo giorno del mese
        return

    # Ottieni tutte le sottoscrizioni attive
    active_subscriptions = UserSubscription.objects.filter(
        is_active=True,
        end_date__gte=today
    )

    for subscription in active_subscriptions:
        # Calcola il periodo di fatturazione
        if subscription.is_annual:
            # Per abbonamenti annuali, il periodo è un mese
            billing_start = start_of_month - timedelta(days=1)
            billing_end = start_of_month.replace(month=billing_start.month % 12 + 1) - timedelta(days=1)
        else:
            # Per abbonamenti mensili
            billing_start = start_of_month - timedelta(days=start_of_month.day)
            billing_end = start_of_month - timedelta(days=1)

        # Crea la fattura
        invoice_number = f"INV-{subscription.user.id}-{today.strftime('%Y%m')}"

        invoice = Invoice.objects.create(
            user=subscription.user,
            subscription=subscription,
            billing_period_start=billing_start,
            billing_period_end=billing_end,
            invoice_date=today,
            due_date=today + timedelta(days=15),
            base_amount=subscription.plan.price_monthly if not subscription.is_annual else subscription.plan.price_yearly / 12,
            storage_extra_amount=subscription.extra_storage_charges,
            queries_extra_amount=subscription.extra_queries_charges,
            total_storage_used_mb=subscription.current_storage_used_mb,
            total_files_count=subscription.current_files_count,
            total_rag_queries=subscription.current_month_rag_queries,
            invoice_number=invoice_number,
            status='pending'
        )

        # Calcola il totale
        invoice.total_amount = invoice.base_amount + invoice.storage_extra_amount + invoice.queries_extra_amount
        invoice.save()

        # Crea gli elementi della fattura
        InvoiceItem.objects.create(
            invoice=invoice,
            description=f"Abbonamento {subscription.plan.name}",
            quantity=1,
            unit_price=invoice.base_amount,
            amount=invoice.base_amount,
            item_type='subscription'
        )

        if invoice.storage_extra_amount > 0:
            InvoiceItem.objects.create(
                invoice=invoice,
                description="Storage aggiuntivo",
                quantity=max(0, subscription.current_storage_used_mb - subscription.plan.storage_limit_mb),
                unit_price=subscription.plan.extra_storage_price_per_mb,
                amount=invoice.storage_extra_amount,
                item_type='storage'
            )

        if invoice.queries_extra_amount > 0:
            InvoiceItem.objects.create(
                invoice=invoice,
                description="Query RAG aggiuntive",
                quantity=max(0, subscription.current_month_rag_queries - subscription.plan.monthly_rag_queries),
                unit_price=subscription.plan.extra_rag_query_price,
                amount=invoice.queries_extra_amount,
                item_type='queries'
            )

        # Resetta i contatori extra
        subscription.extra_storage_charges = 0
        subscription.extra_queries_charges = 0
        subscription.save(update_fields=['extra_storage_charges', 'extra_queries_charges'])