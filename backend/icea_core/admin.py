from django.contrib import admin

from .models import (
    Hospital,
    ICEAComputation,
    ICEAPlusComputation,
    ICEAPlusFormulaVersion,
    ModelArtifact,
    PatientEpisode,
    Unit,
)

admin.site.register(Hospital)
admin.site.register(Unit)
admin.site.register(PatientEpisode)
admin.site.register(ModelArtifact)
admin.site.register(ICEAComputation)
admin.site.register(ICEAPlusFormulaVersion)
admin.site.register(ICEAPlusComputation)
