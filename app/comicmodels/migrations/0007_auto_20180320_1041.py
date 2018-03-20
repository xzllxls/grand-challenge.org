# -*- coding: utf-8 -*-
# Generated by Django 1.11.11 on 2018-03-20 10:41
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('comicmodels', '0006_auto_20180319_1804'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AlterUniqueTogether(
                name='registrationrequest',
                unique_together=set([]),
            ),
            migrations.RemoveField(
                model_name='registrationrequest',
                name='challenge',
            ),
            migrations.RemoveField(
                model_name='registrationrequest',
                name='user',
            ),
            migrations.DeleteModel(
                name='RegistrationRequest',
            ),
        ])
    ]
