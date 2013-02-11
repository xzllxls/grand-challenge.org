import pdb
import os
import re
from urlparse import urlparse, urlunparse
from datetime import datetime

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext


from comicsite.views import getSite
from comicmodels.forms import UploadForm,UserUploadForm
from comicmodels.models import UploadModel,Page

try:
    from PIL import Image, ImageOps
except ImportError:
    import Image
    import ImageOps

try:
    from django.views.decorators.csrf import csrf_exempt
except ImportError:
    # monkey patch this with a dummy decorator which just returns the
    # same function (for compatability with pre-1.1 Djangos)
    def csrf_exempt(fn):
        return fn

THUMBNAIL_SIZE = (75, 75)


def get_available_name(name):
    """
    Returns a filename that's free on the target storage system, and
    available for new content to be written to.
    """
    dir_name, file_name = os.path.split(name)
    file_root, file_ext = os.path.splitext(file_name)
    # If the filename already exists, keep adding an underscore (before the
    # file extension, if one exists) to the filename until the generated
    # filename doesn't exist.
    while os.path.exists(name):
        file_root += '_'
        # file_ext includes the dot.
        name = os.path.join(dir_name, file_root + file_ext)
    return name


def get_thumb_filename(file_name):
    """
    Generate thumb filename by adding _thumb to end of
    filename before . (if present)
    """
    return '%s_thumb%s' % os.path.splitext(file_name)


def create_thumbnail(filename):
    image = Image.open(filename)

    # Convert to RGB if necessary
    # Thanks to Limodou on DjangoSnippets.org
    # http://www.djangosnippets.org/snippets/20/
    if image.mode not in ('L', 'RGB'):
        image = image.convert('RGB')

    # scale and crop to thumbnail
    imagefit = ImageOps.fit(image, THUMBNAIL_SIZE, Image.ANTIALIAS)
    imagefit.save(get_thumb_filename(filename))


def get_media_url(path):
    """
    Determine system file's media URL.
    """
    upload_prefix = getattr(settings, "CKEDITOR_UPLOAD_PREFIX", None)
    if upload_prefix:
        url = upload_prefix + path.replace(settings.CKEDITOR_UPLOAD_PATH, '')
    else:
        url = settings.MEDIA_URL + path.replace(settings.MEDIA_ROOT, '')

    # Remove multiple forward-slashes from the path portion of the url.
    # Break url into a list.
    url_parts = list(urlparse(url))
    # Replace two or more slashes with a single slash.
    url_parts[2] = re.sub('\/+', '/', url_parts[2])
    # Reconstruct the url.
    url = urlunparse(url_parts)

    return url


def get_upload_filename(upload_name, user):
    # If CKEDITOR_RESTRICT_BY_USER is True upload file to user specific path.
    if getattr(settings, 'CKEDITOR_RESTRICT_BY_USER', False):
        user_path = user.username
    else:
        user_path = ''

    # Generate date based path to put uploaded file.
    date_path = datetime.now().strftime('%Y/%m/%d')

    # Complete upload path (upload_path + date_path).
    upload_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path, \
            date_path)

    # Make sure upload_path exists.
    if not os.path.exists(upload_path):
        os.makedirs(upload_path)

    # Get available name and return.
    return get_available_name(os.path.join(upload_path, upload_name))


@csrf_exempt
def upload(request):
    """
    Uploads a file and send back its URL to CKEditor.

    TODO:
        Validate uploads
    """
    # Get the uploaded file from request.
    upload = request.FILES['upload']
    upload_ext = os.path.splitext(upload.name)[1]

    # Open output file in which to store upload.
    upload_filename = get_upload_filename(upload.name, request.user)
    out = open(upload_filename, 'wb+')

    # Iterate through chunks and write to destination.
    for chunk in upload.chunks():
        out.write(chunk)
    out.close()

    create_thumbnail(upload_filename)

    # Respond with Javascript sending ckeditor upload url.
    url = get_media_url(upload_filename)
    return HttpResponse("""
    <script type='text/javascript'>
        window.parent.CKEDITOR.tools.callFunction(%s, '%s');
    </script>""" % (request.GET['CKEditorFuncNum'], url))
    

@csrf_exempt
def upload_to_project(request,site_short_name):
    """
    Uploads a file and send back its URL to CKEditor.
    Uploads to a project directory
    """
    
    # set values excluded from form here to make the model validate
    site = getSite(site_short_name)
    uploadedfile = UploadModel(comicsite=site,permission_lvl = UploadModel.ALL,
                                   user=request.user,file=request.FILES["upload"])
    form = UserUploadForm(request.POST, request.FILES, instance=uploadedfile)
    
    if form.is_valid():
        form.save()
        
        
        # Respond with Javascript sending ckeditor upload url.
        # reverhttp://localhost:8000/site/vessel12/serve/uploads/vesselScreenshot_2.PNG/
        url = get_media_url_project(site_short_name,uploadedfile.title)        
        return HttpResponse("""
        <script type='text/javascript'>
        window.parent.CKEDITOR.tools.callFunction(%s, '%s');
        </script>""" % (request.GET['CKEditorFuncNum'], url))
        
        
    else:
        url = "Uploading failed"
        return HttpResponse("""
        <script type='text/javascript'>
        window.parent.CKEDITOR.tools.callFunction(%s, '%s');
        </script>""" % (request.GET['CKEditorFuncNum'], url))

    # create_thumbnail(upload_filename)
    

def get_media_url_project(projectname,filename):
    return reverse("comicsite.views.inserted_file",kwargs={"site_short_name":projectname,\
                                                           "filepath":os.path.join("uploads",filename)})
                                               

def get_image_files(user=None):
    """
    Recursively walks all dirs under upload dir and generates a list of
    full paths for each file found.
    """
    # If a user is provided and CKEDITOR_RESTRICT_BY_USER is True,
    # limit images to user specific path, but not for superusers.
    if user and not user.is_superuser and getattr(settings, \
            'CKEDITOR_RESTRICT_BY_USER', False):
        user_path = user.username
    else:
        user_path = ''

    browse_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path)

    for root, dirs, files in os.walk(browse_path):
        for filename in [os.path.join(root, x) for x in files]:
            # bypass for thumbs
            if os.path.splitext(filename)[0].endswith('_thumb'):
                continue
            yield filename

@csrf_exempt
def browse_project(request,site_short_name):
    """
    Uploads a file and send back its URL to CKEditor.
    Uploads to a project directory
    """
    
    context = RequestContext(request, {
        'images': get_image_browse_urls_project(site_short_name,request.user),
    })
    return render_to_response('browse.html', context)

        


def get_image_browse_urls(user=None):
    """
    Recursively walks all dirs under upload dir and generates a list of
    thumbnail and full image URL's for each file found.
    """
    images = []
    for filename in get_image_files(user=user):
        images.append({
            'thumb': "",#get_media_url(get_thumb_filename(filename)),
            'src': get_media_url(filename)
        })

    return images

def get_image_browse_urls_project(site_short_name,user=None):
    """
    Recursively walks all dirs under upload dir and generates a list of
    thumbnail and full image URL's for each file found.
    
    FIXME: Sjoerd: getting files by walking a dir is a major design decision.
    Alternative is to have file objects in db and list those. Problem there
    is that it is almost impossible to keep db file object up to date if we
    allow dropbox interaction for example. Reflecting file dir directly as 
    done below is more intueitive and less error prone. Losing context
    context (who uploaded, which rights?) is an exceptable sacrifice.
    """
    images = []
    #get all uploadmodels for this user and site,
    site = getSite(site_short_name)
    
    
    for root, dirs, files in os.walk(site.upload_dir()):
        for filename in [os.path.join(root, x) for x in files]:
            # bypass for thumbs
            if os.path.splitext(filename)[0].endswith('_thumb'):
                continue
            images.append({
                'thumb': get_media_url_project(site_short_name,filename),
                'src': get_media_url_project(site_short_name,filename)
            })
        
    return images


def browse(request):
    context = RequestContext(request, {
        'images': get_image_browse_urls(request.user),
    })
    return render_to_response('browse.html', context)