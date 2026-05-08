FROM ubuntu:22.04

RUN sed -i 's|http://archive.ubuntu.com|http://kh.archive.ubuntu.com|g; s|http://security.ubuntu.com|http://kh.archive.ubuntu.com|g' /etc/apt/sources.list && \
    apt update -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3.10 \
        python3.10-dev \
        python3-pip \
        default-libmysqlclient-dev \
        build-essential \
        pkg-config \
    && apt-get clean \
    && ln -sf /usr/bin/python3.10 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.10 /usr/bin/python

# set work directory
WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH "${PYTHONPATH}:/usr/src/app/src"

# install dependencies
RUN pip install --upgrade pip
COPY ./requirements.txt /usr/src/app/requirements.txt
RUN pip install -r requirements.txt
# --use-deprecated=legacy-resolver

# copy entrypoint.sh
COPY ./entrypoint.sh /usr/src/app/entrypoint.sh

# copy project
COPY . /usr/src/app/

# run entrypoint.sh
ENTRYPOINT ["/usr/src/app/entrypoint.sh"]
