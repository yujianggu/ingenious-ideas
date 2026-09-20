import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("identity", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="CommandReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(max_length=100)),
                ("object_id", models.CharField(default="", max_length=64)),
                ("key", models.CharField(max_length=200)),
                ("request_hash", models.CharField(max_length=64)),
                ("result_json", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="command_receipts", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="command_receipts", to="identity.tenant")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("tenant", "actor", "operation", "object_id", "key"), name="unique_command_receipt_scope_key")]},
        )
    ]
