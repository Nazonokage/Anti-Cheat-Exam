from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0010_submission_review_order")]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="prompt_language",
            field=models.CharField(
                choices=[
                    ("en", "English"),
                    ("zh", "Chinese"),
                    ("ar", "Arabic"),
                    ("ru", "Russian"),
                ],
                default="en",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="randomize_prompt_language",
            field=models.BooleanField(default=False, verbose_name="Randomize Prompt Language"),
        ),
    ]
