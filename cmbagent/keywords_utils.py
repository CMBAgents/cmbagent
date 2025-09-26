import json



class UnescoKeywords:

    def __init__(self, unesco_taxonomy_path):
        self.unesco_dict = json.load(open(unesco_taxonomy_path))
        self.n_keywords_level1 = 3 # how many to pick from level 1 (11)
        self.n_keywords_level2 = 4 # how many to pick from level 2 (1101, 1102)
        self.n_keywords_level3 = 6 # how many to pick from level 3 (1102.01, 1102.02)

    def get_unesco_level1_names(self):
        """Return a list of all level-1 field names."""
        return [info['name'] for info in self.unesco_dict.values()]

    def get_unesco_level2_names(self, level1_name):
        """Return all level-2 names under a given level-1 name."""
        for info in self.unesco_dict.values():
            if info['name'] == level1_name:
                return [sub['name'] for sub in info.get('sub_fields', {}).values()]
        return []

    def get_unesco_level3_names(self, level2_name):
        """Return all level-3 names under a given level-2 name."""
        for level1_info in self.unesco_dict.values():
            for sub in level1_info.get('sub_fields', {}).values():
                if sub['name'] == level2_name:
                    return [area['name'] for area in sub.get('specific_areas', {}).values()]
        return []


class AaaiKeywords:

    def __init__(self, aaai_keywords_path):
        self.aaai_keywords_string = open(aaai_keywords_path).read()
