from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

from core.services.importer import import_exam_from_file, ImportError_


class Command(BaseCommand):
    help = "Import an exam from a JSON questionnaire file (see data/sampletopic.json)."

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str)
        parser.add_argument(
            "--teacher",
            type=str,
            default=None,
            help="Username of the teacher to attribute this exam to. "
                 "Defaults to the first superuser found.",
        )

    def handle(self, *args, **options):
        json_path = options["json_path"]

        if options["teacher"]:
            try:
                teacher = User.objects.get(username=options["teacher"])
            except User.DoesNotExist:
                raise CommandError(f"No such user: {options['teacher']}")
        else:
            teacher = User.objects.filter(is_superuser=True).first()
            if not teacher:
                raise CommandError(
                    "No superuser found. Create one with 'python manage.py createsuperuser' "
                    "or pass --teacher <username>."
                )

        try:
            exam = import_exam_from_file(json_path, teacher)
        except ImportError_ as e:
            raise CommandError(str(e))
        except FileNotFoundError:
            raise CommandError(f"File not found: {json_path}")

        self.stdout.write(self.style.SUCCESS(
            f"Imported exam '{exam.title}' (id={exam.id}) with "
            f"{exam.questions.count()} questions. It is inactive by default — "
            f"activate it in Django Admin before students can take it."
        ))
