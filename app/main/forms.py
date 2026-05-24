from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length, Regexp, Optional


class CheckInForm(FlaskForm):
    vehicle_number = StringField(
        "Vehicle Number",
        validators=[
            DataRequired(),
            Length(max=20, message="Vehicle number must be 20 characters or fewer."),
            Regexp(
                r'(?i)^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$',
                message="Enter a valid vehicle number, e.g. MH12AB1234 or KA01ABC1234",
            ),
        ],
    )
    submit = SubmitField("Check In")


class ExitForm(FlaskForm):
    """Empty form — provides CSRF token for the payment callback."""
    pass


class AddVehicleForm(FlaskForm):
    """H7: Save a vehicle number (with optional label) for quick check-in."""
    vehicle_number = StringField(
        "Vehicle Number",
        validators=[
            DataRequired(),
            Length(max=20),
            Regexp(
                r'(?i)^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$',
                message="Enter a valid vehicle number, e.g. MH12AB1234 or KA01ABC1234",
            ),
        ],
    )
    label = StringField("Label (optional)", validators=[Optional(), Length(max=50)])
    submit = SubmitField("Save Vehicle")
