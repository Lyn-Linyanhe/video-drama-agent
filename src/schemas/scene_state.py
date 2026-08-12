"""Scene location state schema."""

from pydantic import BaseModel, Field


class LocationState(BaseModel):
    """A concrete visual state of a reusable physical location."""

    state_id: str = Field(description="Stable location-state identifier")
    label: str = Field(default="", description="Display label for the state")
    time_condition: str = Field(default="", description="Time / weather / lighting condition")
    weather: str = Field(default="", description="Weather description")
    light_direction: str = Field(default="", description="Main light direction")
    visible_state_notes: str = Field(default="", description="Temporary visible state notes")
    shot_ids: list[int] = Field(default_factory=list, description="Shots using this state")
    representative_scene_id: int = Field(default=0, description="Representative shot for the state")
