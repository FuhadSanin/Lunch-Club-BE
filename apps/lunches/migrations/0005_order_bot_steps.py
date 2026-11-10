from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lunches', '0004_item_restaurant_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='swiggyorder',
            name='bot_steps',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
