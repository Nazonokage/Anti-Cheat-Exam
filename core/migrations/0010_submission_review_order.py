from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0009_exam_show_review_answers")]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="review_order",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
