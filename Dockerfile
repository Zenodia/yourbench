# Use a base image with Python
FROM python:3.12-slim 

ENV DERBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Stockholm
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
 

# Set working directory
WORKDIR /workspace

RUN apt update -y 
#RUN apt install ffmpeg -y

RUN python -m pip install --upgrade pip

#RUN pip install flash-attn --no-build-isolation
#RUN pip install transformers qwen-vl-utils accelerate
#RUN pip install opencv-fixer==0.2.5
#RUN python -c "from opencv_fixer import AutoFix; AutoFix()"
RUN pip install -U gretel_client datasets langchain-core langgraph langchain-nvidia-ai-endpoints openai langchain-community faiss-gpu-cu12
RUN pip install jupyterlab fastmcp colorama

COPY . /workspace
RUN pip install -e . 
RUN pip install yourbench[semantic]
# Expose port 8888 for JupyterLab
EXPOSE 8888 9999 8000 7860 7861 60808
#RUN export LC_ALL="en_US.UTF-8" export LC_CTYPE="en_US.UTF-8"
#RUN apt install locales -y


# Start JupyterLab when the container runs
CMD ["sh", "-c", "tail -f /dev/null"]
#CMD ["jupyter", "lab", "--allow-root", "--ip=0.0.0.0","--NotebookApp.token=''", "--port=8888"]
