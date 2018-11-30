from io import BytesIO
from PIL import Image as PILImage
from pathlib import Path
import json
from django.conf import settings
from rest_framework import status
from django.contrib.auth import get_user_model
from tests.viewset_helpers import TEST_USER_CREDENTIALS
from tests.datastructures_tests.factories import (
    ArchiveFactory,
    PatientFactory,
    StudyFactory,
    RetinaImageFactory,
)
from grandchallenge.studies.models import Study


# helper functions
def create_test_image():
    """
    Create image for testing purposes
    :return: file
    """
    file = BytesIO()
    image = PILImage.new("RGBA", size=(50, 50), color=(155, 0, 0))
    image.save(file, "png")
    file.name = "test.png"
    file.seek(0)
    return file


def read_json_file(path_to_file):
    path_to_file = Path("/app/tests/retina_importers_tests/test_data") / path_to_file
    print(path_to_file.absolute())
    try:
        file = open(path_to_file, "r")
        if file.mode == "r":
            file_contents = file.read()
            file_object = json.loads(file_contents)
            return file_object
        else:
            raise FileNotFoundError()
    except FileNotFoundError:
        print("Warning: No json file in {}".format(path_to_file))
    return None


def create_upload_image_test_data():
    # create image
    file = create_test_image()
    data = read_json_file("upload_image_valid_data.json")
    # create request payload
    data.update({"image": file})
    return data


def create_upload_image_invalid_test_data():
    # create image
    file = create_test_image()
    data = read_json_file("upload_image_invalid_data.json")
    # create request payload
    data.update({"image": file})
    return data


def remove_test_image(response):
    # Remove uploaded test image from filesystem
    response_obj = json.loads(response.content)
    full_path_to_image = settings.APPS_DIR / Path(response_obj["image"]["image"][1:])
    Path.unlink(full_path_to_image)


def get_response_status(client, url, data, user="anonymous", annotation_data=None):
    # login user
    if user == "staff":
        user = get_user_model().objects.create_superuser(**TEST_USER_CREDENTIALS)
        client.login(**TEST_USER_CREDENTIALS)
    elif user == "normal":
        user = get_user_model().objects.create_user(**TEST_USER_CREDENTIALS)
        client.login(**TEST_USER_CREDENTIALS)

    if annotation_data:
        # create objects that need to exist in database before request is made
        patient = PatientFactory(name=data.get("patient_identifier"))
        existing_models = {"studies": [], "series": [], "images": []}
        images = []
        for data_row in data.get("data"):
            if data_row.get("study_identifier") not in existing_models["studies"]:
                study = StudyFactory(name=data_row.get("study_identifier"), patient=patient)
                existing_models["studies"].append(study.name)
            else:
                study = Study.objects.get(name=data_row.get("study_identifier"))

            if data_row.get("image_identifier") not in existing_models["images"]:
                image = RetinaImageFactory(name=data_row.get("image_identifier"), study=study)
                existing_models["images"].append(image.name)
                images.append(image)
        archive = ArchiveFactory(name=data.get("archive_identifier"), images=images)

        response = client.post(
            url, data=json.dumps(data), content_type="application/json"
        )
    else:
        response = client.post(url, data=data)
    return response.status_code


def create_test_method(url, data, user, expected_status, annotation_data=None):
    def test_method(self, client):
        response_status = get_response_status(client, url, data, user, annotation_data)
        assert response_status == expected_status

    return test_method


def batch_test_upload_views(batch_test_data, test_class):
    for name, test_data in batch_test_data.items():
        user_status_tuple = (
            ("anonymous", status.HTTP_403_FORBIDDEN, status.HTTP_403_FORBIDDEN),
            ("normal", status.HTTP_403_FORBIDDEN, status.HTTP_403_FORBIDDEN),
            ("staff", status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST),
        )
        for user, expected_status_valid, expected_status_invalid in user_status_tuple:
            for valid_data in (False, True):
                post_data = (
                    test_data["data"] if valid_data else test_data["invalid_data"]
                )
                test_method = create_test_method(
                    test_data["url"],
                    post_data,
                    user,
                    expected_status_valid if valid_data else expected_status_invalid,
                    annotation_data=test_data.get("annotation_data"),
                )
                test_method.__name__ = "test_{}_upload_view_{}_{}_data".format(
                    name, user, "valid" if valid_data else "invalid"
                )
                setattr(test_class, test_method.__name__, test_method)