# Generated by Django 2.0.8 on 2018-09-09 05:13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0006_auto_20180830_1524"),
        ("algorithms", "0001_squashed_0004_auto_20180814_1508"),
    ]

    operations = [
        migrations.AddField(
            model_name="algorithm",
            name="slug",
            field=models.SlugField(
                editable=False, max_length=32, null=True, unique=True
            ),
        ),
        migrations.AddField(
            model_name="algorithm",
            name="title",
            field=models.CharField(max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="result",
            name="images",
            field=models.ManyToManyField(
                related_name="algorithm_results", to="cases.Image"
            ),
        ),
    ]
