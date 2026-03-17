#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/stat.h>

#define PORT 8080
#define BUFFER_SIZE 65536

// 協助發送 HTTP 回應標頭與本體
void send_response(int socket, const char *status, const char *content_type, const char *body, int body_len) {
    char header[1024];
    sprintf(header, "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nAccess-Control-Allow-Origin: *\r\n\r\n", status, content_type, body_len);
    send(socket, header, strlen(header), 0);
    if (body_len > 0 && body != NULL) {
        send(socket, body, body_len, 0);
    }
}

// 傳送靜態或文字檔案
void send_file(int socket, const char *filepath, const char *content_type) {
    FILE *f = fopen(filepath, "rb");
    if (!f) {
        send_response(socket, "404 Not Found", "text/plain", "File not found", 14);
        return;
    }
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *body = malloc(fsize);
    if (!body) {
        fclose(f);
        send_response(socket, "500 Internal Error", "text/plain", "Malloc failed", 13);
        return;
    }
    
    int bytes_read = fread(body, 1, fsize, f);
    fclose(f);

    send_response(socket, "200 OK", content_type, body, bytes_read);
    free(body);
}

void handle_client(int client_socket) {
    char *buffer = malloc(BUFFER_SIZE);
    memset(buffer, 0, BUFFER_SIZE);
    
    // 讀取要求標頭
    int received = recv(client_socket, buffer, BUFFER_SIZE - 1, 0);
    if (received <= 0) {
        free(buffer);
        close(client_socket);
        return;
    }
    
    char method[16] = {0}, path[256] = {0};
    sscanf(buffer, "%15s %255s", method, path);
    printf("Request: %s %s\n", method, path);
    
    if (strcmp(method, "GET") == 0) {
        if (strcmp(path, "/") == 0 || strcmp(path, "/index.html") == 0) {
            send_file(client_socket, "index.html", "text/html; charset=utf-8");
        } else if (strcmp(path, "/data.json") == 0) {
            send_file(client_socket, "data.json", "application/json; charset=utf-8");
        } else if (strstr(path, "/favicon.ico")) {
             send_response(client_socket, "404 Not Found", "text/plain", "", 0);
        } else {
            send_response(client_socket, "404 Not Found", "text/plain", "Not found", 9);
        }
    } else if (strcmp(method, "POST") == 0 && strcmp(path, "/data.json") == 0) {
        char *cl_ptr = strstr(buffer, "Content-Length: ");
        int content_length = 0;
        if (cl_ptr) {
            content_length = atoi(cl_ptr + 16);
        }
        
        char *body_start = strstr(buffer, "\r\n\r\n");
        if (body_start) {
            body_start += 4;
            int body_received = received - (body_start - buffer);
            
            FILE *f = fopen("data.json", "wb");
            if (f) {
                if (body_received > 0) {
                    fwrite(body_start, 1, body_received, f);
                }
                
                int total_body_received = body_received;
                // 若檔案過大，繼續從 socket 讀取剩餘本體
                while (total_body_received < content_length) {
                    int recv_len = recv(client_socket, buffer, BUFFER_SIZE, 0);
                    if (recv_len <= 0) break;
                    fwrite(buffer, 1, recv_len, f);
                    total_body_received += recv_len;
                }
                fclose(f);
                send_response(client_socket, "200 OK", "application/json", "{\"status\":\"ok\"}", 15);
            } else {
                send_response(client_socket, "500 Internal Error", "text/plain", "file error", 10);
            }
        } else {
            send_response(client_socket, "400 Bad Request", "text/plain", "Too large header", 16);
        }
    } else if (strcmp(method, "OPTIONS") == 0) {
        char resp[] = "HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\n\r\n";
        send(client_socket, resp, strlen(resp), 0);
    } else {
        send_response(client_socket, "405 Method Not Allowed", "text/plain", "", 0);
    }
    
    free(buffer);
    close(client_socket);
}

int main() {
    int server_fd, new_socket;
    struct sockaddr_in address;
    int opt = 1;
    int addrlen = sizeof(address);

    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == 0) {
        perror("socket failed");
        exit(EXIT_FAILURE);
    }

    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(PORT);

    if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        perror("bind failed");
        exit(EXIT_FAILURE);
    }

    if (listen(server_fd, 5) < 0) {
        perror("listen");
        exit(EXIT_FAILURE);
    }

    printf("Server 正在 Port %d 穩定輸出中...\n", PORT);
    printf("請開啟瀏覽器進入 http://localhost:%d\n", PORT);

    while(1) {
        if ((new_socket = accept(server_fd, (struct sockaddr *)&address, (socklen_t*)&addrlen)) < 0) {
            perror("accept");
            continue;
        }
        
        handle_client(new_socket);
    }

    return 0;
}
