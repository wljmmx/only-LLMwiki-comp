-- 创建数据库和用户
CREATE DATABASE IF NOT EXISTS ops_db;
CREATE USER 'ops'@'%' IDENTIFIED BY 'secure_pass';

-- 创建表
CREATE TABLE IF NOT EXISTS servers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    hostname VARCHAR(255) NOT NULL,
    ip VARCHAR(45),
    status ENUM('running', 'stopped') DEFAULT 'running'
);

-- 插入数据
INSERT INTO servers (hostname, ip) VALUES ('web-01', '10.0.0.1');

-- 查询
SELECT * FROM servers WHERE status = 'running';
