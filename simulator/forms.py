from django import forms

from simulator.models import Unit


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = [
            'name',
            'unit_type',
            'team',
            'health',
            'attack_points',
            'profile_image_link',
        ]
        labels = {
            'name': 'Name',
            'unit_type': 'Type',
            'team': 'Team',
            'health': 'Health',
            'attack_points': 'AttackPoints',
            'profile_image_link': 'ProfileImageLink',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Sir Aldric'}),
            'unit_type': forms.Select(),
            'team': forms.Select(),
            'health': forms.NumberInput(attrs={'min': 0, 'max': 100}),
            'attack_points': forms.NumberInput(attrs={'min': 0, 'max': 50}),
            'profile_image_link': forms.URLInput(
                attrs={'placeholder': 'https://example.com/avatar.png'}
            ),
        }
