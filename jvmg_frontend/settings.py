"""
Django settings for jvmg_frontend project.

Generated by 'django-admin startproject' using Django 3.0.3.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.0/ref/settings/
"""

import os

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
with open('secret_key.txt') as f:
    SECRET_KEY = f.read().strip()

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['127.0.0.1', "localhost"]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'jvmg'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'jvmg_frontend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'jvmg_frontend.wsgi.application'


# Database
# https://docs.djangoproject.com/en/3.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/3.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Europe/Berlin'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.0/howto/static-files/

STATIC_URL = '/static/'
# STATIC_ROOT = '/opt/static/'

# project specific
SLOW_LOG_THRESHOLD = 1.0 # threshold for slow log: how long (in seconds) a query can take until it is logged into the slow log 
SPARQL_ENDPOINT = "http://localhost:3030/jvmg"
DATASET_BASE = "http://mediagraph.link/"
#WEB_BASE = "http://mediagraph.link/"
WEB_BASE = "http://127.0.0.1:8003/"
LABEL_URIS = ["http://www.w3.org/2000/01/rdf-schema#label"]
GRAPH_LABEL_URIS = ["http://mediagraph.link/jvmg/ont/shortLabel"]

# graphs which should not be displayed by default
NSFW_GRAPHS = ["http://mediagraph.link/graph/vndb_nsfw"]

QUERY = """
PREFIX label: <http://www.w3.org/2000/01/rdf-schema#label>
PREFIX graph_label: <http://mediagraph.link/jvmg/ont/shortLabel>

CONSTRUCT {
  Graph ?graph {
    ?s ?p ?o .
    ?o ?p_blank ?o_blank .
    ?graph graph_label: ?graph_label .
    ?p_blank label: ?p_blank_label .
    ?o_blank label: ?o_blank_label .
  }
  ?s label: ?s_label .
  ?p label: ?p_label .
  ?o label: ?o_label .

} where {
  {
    GRAPH ?graph {
      ?s ?p ?o . filter(?s = <$resource>)
      OPTIONAL { ?o ?p_blank ?o_blank filter isBlank(?o)
        OPTIONAL { ?p_blank label: ?p_blank_label }
        OPTIONAL { ?o_blank label: ?o_blank_label }
      }

    }
    OPTIONAL { ?graph graph_label: ?graph_label}
    OPTIONAL { ?o label: ?o_label}
    OPTIONAL { ?p label: ?p_label}
  }
  UNION
  {
    GRAPH ?graph {
      ?s ?p ?o . filter(?o = <$resource>)
      OPTIONAL { ?graph graph_label: ?graph_label }
    }
    OPTIONAL { ?s label: ?s_label}
    OPTIONAL { ?p label: ?p_label}
  }
}
"""

ELASTICSEARCH = "http://127.0.0.1:9200"


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '%(levelname)s %(asctime)s %(module)s %(message)s'
        },
        'simple': {
            'format': '%(levelname)s %(asctime)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'access.log',
            'formatter': 'verbose'
        },
        'slow': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'slow.log',
            'formatter': 'verbose'
        },
    },
    'loggers': {
        'default': {
            'handlers': ['file'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': True,
        },
        'slow': {
            'handlers': ['slow'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': True,
        },
    },
}
