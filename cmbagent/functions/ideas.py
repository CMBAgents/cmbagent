"""Functions for recording and managing ideas."""

import os
import json
import datetime


def create_record_ideas(cmbagent_instance):
    """Factory function to create record_ideas with cmbagent instance."""

    def record_ideas(ideas: list):
        """Record ideas. You must record the entire list of ideas and their descriptions. You must not alter the list."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(cmbagent_instance.work_dir, f'ideas_{timestamp}.json')
        with open(filepath, 'w') as f:
            json.dump(ideas, f)

        return f"\nIdeas saved in {filepath}\n"

    return record_ideas
