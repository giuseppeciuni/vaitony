# Generated by Django 5.1.1 on 2025-05-07 21:12

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='answersource',
            name='project_url',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='used_in_answers_from_urls', to='profiles.projecturl'),
        ),
        migrations.AlterField(
            model_name='answersource',
            name='project_file',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='used_in_answers', to='profiles.projectfile'),
        ),
    ]
