from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('memory_engine', '0007_reminder_twilio_sid'),
    ]

    operations = [
        migrations.AddField(
            model_name='botuser',
            name='reminder_repeat_minutes',
            field=models.IntegerField(default=0),
        ),
    ]
