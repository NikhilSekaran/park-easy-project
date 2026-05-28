from flask_wtf import FlaskForm
from wtforms import IntegerField, DecimalField, SubmitField, SelectField, TextAreaField, StringField, BooleanField
from wtforms.validators import DataRequired, NumberRange, InputRequired, Optional, Length


class AddSpotForm(FlaskForm):
    spot_number = IntegerField(
        "Spot Number",
        validators=[DataRequired(), NumberRange(min=1, message="Spot number must be positive.")],
    )
    submit = SubmitField("Add Spot")


class PricingForm(FlaskForm):
    hourly_rate = DecimalField(
        "Hourly Rate (₹)",
        validators=[DataRequired(), NumberRange(min=0.01, message="Rate must be greater than zero.")],
        places=2,
    )
    grace_minutes = IntegerField(
        "Grace Period (minutes)",
        validators=[InputRequired(), NumberRange(min=0, max=60, message="Between 0 and 60.")],
        default=0,
    )  # G3: free window before billing starts; 0 disables grace period
    submit = SubmitField("Update Rate")


class AddSpotsBulkForm(FlaskForm):
    start_number = IntegerField(
        "Start Spot Number",
        validators=[DataRequired(), NumberRange(min=1, message="Must be positive.")],
    )
    count = IntegerField(
        "Number of Spots to Add",
        validators=[DataRequired(), NumberRange(min=1, max=100, message="Between 1 and 100.")],
    )
    submit = SubmitField("Add Spots")


class BulkToggleSpotsForm(FlaskForm):
    """CSRF-only form for the bulk activate/deactivate action.
    spot_ids and action are read directly from request.form."""
    pass


class AnnouncementForm(FlaskForm):
    """H4: Form for admin to create/update the site-wide announcement banner."""
    message = TextAreaField("Message", validators=[DataRequired(), Length(max=280)])
    is_active = BooleanField("Show to users", default=True)
    submit = SubmitField("Save")


class SessionNoteForm(FlaskForm):
    """H9: Inline note form for admin to annotate individual sessions."""
    note = StringField("Note", validators=[Optional(), Length(max=200)])
    submit = SubmitField("Save")
